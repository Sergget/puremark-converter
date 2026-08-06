#!/usr/bin/env bash
# ============================================================
# deploy_ubuntu.sh — Ubuntu 调度节点「可选组件」部署脚本
#
# 适用于：分布式文档转换系统的 Ubuntu（轻量）节点
# 目标主机：192.168.0.24（J3455，4GB 内存，Ubuntu Server 20.04）
# 代码路径：/var/www/markitdown（被 systemd 服务引用）
#
# 部署组件（可单独或组合指定，不传参数时默认部署全部 all）：
#   app      调度代码（main_server/ 下所有内容：app.py、requirements.txt 等）
#   html     前端裁剪 UI 页面（html/）
#   service  systemd 服务单元 markitdown.service（部署后会自动重启服务）
#   nginx    Nginx 反向代理配置 markitdown.conf
#   all      以上全部
#
# 用法：
#   ./deploy_ubuntu.sh                  # 全量部署（等价于 ./deploy_ubuntu.sh all）
#   ./deploy_ubuntu.sh app              # 只更新调度代码并重启服务（改 app.py 时最常用）
#   ./deploy_ubuntu.sh html             # 只更新前端页面（静态页面无需重启服务）
#   ./deploy_ubuntu.sh service          # 只更新 systemd 单元并重启服务
#   ./deploy_ubuntu.sh nginx            # 只更新 Nginx 配置并 reload
#   ./deploy_ubuntu.sh app html         # 组合：同时更新代码 + 前端
#   ./deploy_ubuntu.sh -h|--help        # 查看本帮助
#
# 示例：
#   # 只改了 main_server/app.py（例如调整输出格式/OCR 路由）：
#   ./deploy_ubuntu.sh app
#   # 只改了前端裁剪 UI：
#   ./deploy_ubuntu.sh html
#   # 同时改了 app.py 和前端：
#   ./deploy_ubuntu.sh app html
#
# 说明：
#   - app / service 组件部署完成后会自动 restart 服务，使改动立即生效；
#   - html / nginx 组件为静态/代理配置，不影响运行中的进程，不触发重启；
#   - nginx 的 markitdown.conf 若不存在（已被移除/迁移），仅打印警告，不中断部署。
#
# 阶段状态：阶段 6 ✅（前后端联调完成）
# 详见：分布式文档转换系统_实施计划与测试路线.md
# ============================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="/var/www/markitdown"           # 部署目标根目录（systemd 与 nginx 均引用此路径）
HTML_DIR="$TARGET_DIR/html"                # 前端静态页面目录
SERVICE_NAME="markitdown"                  # systemd 服务名，与 markitdown.service 的 [Unit] 对应
SERVICE_SRC="$ROOT_DIR/main_server/markitdown.service"
NGINX_CONF_SRC="$ROOT_DIR/main_server/markitdown.conf"
SYSTEMD_DEST="/etc/systemd/system/${SERVICE_NAME}.service"
NGINX_AVAILABLE="/etc/nginx/sites-available/${SERVICE_NAME}"
NGINX_ENABLED="/etc/nginx/sites-enabled/${SERVICE_NAME}"

KNOWN_PARTS="app html service nginx all"
PART_COUNT="$#"

# ---- 帮助 / 用法 ----
usage() {
    sed -n '2,41p' "$0" | sed 's/^# \?//' | sed 's/^/  /'
}
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

# ---- 解析要部署的组件 ----
# 不传参数 = all；传了参数则只部署列出的组件（重复项自动忽略）。
declare -A PARTS
if [ "$PART_COUNT" -eq 0 ]; then
    PARTS[all]=1
else
    for p in "$@"; do
        case " $KNOWN_PARTS " in
            *" $p "*) PARTS["$p"]=1 ;;
            *)
                echo "Error: unknown component '$p'" >&2
                echo "Usage: $0 [app] [html] [service] [nginx] [all]" >&2
                exit 1
                ;;
        esac
    done
fi

# need <name>：判断某组件是否需要部署（all 模式下全部为真）
need() { [ -n "${PARTS[all]:-}" ] || [ -n "${PARTS[$1]:-}" ]; }

# ---- 重启服务（app / service 部署后调用，使改动生效）----
restart_service() {
    if systemctl list-unit-files 2>/dev/null | grep -q "^${SERVICE_NAME}.service"; then
        sudo systemctl daemon-reload
        sudo systemctl enable --now "$SERVICE_NAME" 2>/dev/null || true
        sudo systemctl restart "$SERVICE_NAME"
        echo ">>> 已重启服务 ${SERVICE_NAME}"
    else
        echo "Warning: 服务 ${SERVICE_NAME}.service 尚未安装，跳过重启" >&2
    fi
}

# ============================================================
#  组件 1：调度代码（app.py 等）
# ============================================================
deploy_app() {
    echo ">>> [app] 部署调度代码 main_server/ -> $TARGET_DIR"
    # install -d 同时设置 www-data 属主，确保 app.py 运行时对目录有写权限
    # （www-data 也是 markitdown.service 中 User 字段指定的用户）
    sudo install -d -o www-data -g www-data "$TARGET_DIR"
    # 拷贝 main_server/ 下所有内容（含 app.py、html/、requirements.txt 等）
    sudo cp -a "$ROOT_DIR/main_server/." "$TARGET_DIR/"
    sudo chown -R www-data:www-data "$TARGET_DIR"
    # 代码更新后必须重启服务，否则运行中的进程仍是旧代码
    restart_service
}

# ============================================================
#  组件 2：前端页面（html/）
# ============================================================
deploy_html() {
    echo ">>> [html] 部署前端页面 html/ -> $HTML_DIR"
    sudo install -d -o www-data -g www-data "$HTML_DIR"
    sudo cp -a "$ROOT_DIR/main_server/html/." "$HTML_DIR/"
    sudo chown -R www-data:www-data "$HTML_DIR"
    # 前端为静态文件，由 Flask 每次请求读取，无需重启服务
}

# ============================================================
#  组件 3：systemd 服务单元
# ============================================================
deploy_service() {
    echo ">>> [service] 安装 systemd 服务单元"
    # markitdown.service 定义了环境变量 NODE_NAME/NODE_ROLE/PORT/WIN11_OCR_URL、
    # 网络依赖与重启策略（Restart=on-failure / RestartSec=3）
    if [ -f "$SERVICE_SRC" ]; then
        sudo install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DEST"
    else
        echo "Warning: service file not found: $SERVICE_SRC" >&2
    fi
    # 单元文件变化后必须 daemon-reload，再重启服务生效
    restart_service
}

# ============================================================
#  组件 4：Nginx 反向代理配置
# ============================================================
deploy_nginx() {
    echo ">>> [nginx] 安装 Nginx 反向代理配置"
    # markitdown.conf 将 md.sergget.qzz.io 的 HTTPS 流量代理到 127.0.0.1:5000
    # （app.py 监听的内部端口，默认 5000，可由 PORT 环境变量覆盖）
    # SSL 证书位于 /etc/nginx/snippets/ssl-sergget.conf（需独立维护）
    if [ -f "$NGINX_CONF_SRC" ]; then
        sudo install -m 0644 "$NGINX_CONF_SRC" "$NGINX_AVAILABLE"
        sudo ln -sf "$NGINX_AVAILABLE" "$NGINX_ENABLED"
        if command -v nginx >/dev/null 2>&1; then
            # 验证配置语法，不阻止后续流程
            sudo nginx -t >/dev/null 2>&1 || true
            # 优先 reload（不中断现有连接），失败则 fallback 到 restart
            if systemctl list-unit-files 2>/dev/null | grep -q '^nginx.service'; then
                sudo systemctl reload nginx 2>/dev/null || sudo systemctl restart nginx 2>/dev/null || true
            fi
        fi
    else
        echo "Warning: nginx config not found: $NGINX_CONF_SRC" >&2
    fi
}

# ============================================================
#  按需执行各组件
# ============================================================
if need app; then
    deploy_app
fi
if need html; then
    deploy_html
fi
if need service; then
    deploy_service
fi
if need nginx; then
    deploy_nginx
fi

# ============================================================
#  部署摘要
# ============================================================
printf '===== PureMark Ubuntu 部署完成 =====\n'
printf '本次部署组件：'
if [ -n "${PARTS[all]:-}" ]; then
    printf 'all\n'
else
    printf '%s\n' "$*"
fi
printf '调度入口（app.py）：%s\n' "$TARGET_DIR/app.py"
printf '前端 UI 页面：       %s\n' "$HTML_DIR"
printf 'Nginx 配置文件：     %s\n' "$NGINX_AVAILABLE"
printf 'systemd 服务：       %s （已重启）\n' "$SYSTEMD_DEST"
printf '\n验证命令：\n'
printf '  systemctl status %s\n' "$SERVICE_NAME"
printf '  curl http://127.0.0.1:5000/health\n'
printf '  curl https://md.sergget.qzz.io/health\n'
printf '=====================================\n'
