#!/usr/bin/env bash
# ============================================================
# deploy_ubuntu.sh — Ubuntu 调度节点一键部署脚本
#
# 适用于：分布式文档转换系统的 Ubuntu（轻量）节点
# 目标主机：192.168.0.24（J3455，4GB 内存，Ubuntu Server 20.04）
# 代码路径：/var/www/markitdown（被 systemd 服务引用）
#
# 部署内容：
#   1. app.py              — 调度层（路由 + 容灾切换 + 版面重建）
#   2. markitdown.service  — systemd 常驻服务单元
#   3. markitdown.conf     — Nginx 反向代理配置（HTTPS 末端）
#   4. html/               — 前端裁剪 UI 页面
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

# ---- 1. 创建目录并拷贝代码 ----
# 注意：install -d 同时设置了 www-data 属主，确保 app.py 运行时对 /var/www/markitdown
# 目录有写入权限（如需写临时文件或日志），www-data 也是 markitdown.service 中 User 字段指定的用户。
sudo install -d -o www-data -g www-data "$TARGET_DIR" "$HTML_DIR"

# 拷贝 main_server/ 下所有内容（含 app.py、html/、requirements.txt 等）
sudo cp -a "$ROOT_DIR/main_server/." "$TARGET_DIR/"
# 单独拷贝 html/ 子目录（确保前端页面在正确的子目录结构下）
sudo cp -a "$ROOT_DIR/main_server/html/." "$HTML_DIR/"

# ---- 2. 修复文件属主 ----
sudo chown -R www-data:www-data "$TARGET_DIR"
sudo chown -R www-data:www-data "$HTML_DIR"

# ---- 3. 安装 systemd 服务单元 ----
# markitdown.service 定义了：
#   - 环境变量：NODE_NAME=ubuntu, NODE_ROLE=light（调度器据此识别节点身份）
#   - 依赖：network.target（网络就绪后启动）
#   - 重启策略：on-failure（非正常退出时自动拉起，间隔 3 秒）
if [ -f "$SERVICE_SRC" ]; then
    sudo install -m 0644 "$SERVICE_SRC" "$SYSTEMD_DEST"
else
    echo "Warning: service file not found: $SERVICE_SRC" >&2
fi

# ---- 4. 安装 Nginx 反向代理配置 ----
# markitdown.conf 将 md.sergget.qzz.io 的 HTTPS 流量代理到本机 127.0.0.1:5000
# （app.py 监听的内部端口，默认 5000，可通过 PORT 环境变量覆盖）
# SSL 证书位于 /etc/nginx/snippets/ssl-sergget.conf（需独立维护）
if [ -f "$NGINX_CONF_SRC" ]; then
    # 拷贝到 sites-available
    sudo install -m 0644 "$NGINX_CONF_SRC" "$NGINX_AVAILABLE"
    # 创建软连接到 sites-enabled
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

# ---- 5. 重载 systemd 并启用服务 ----
sudo systemctl daemon-reload
# enable --now 等价于 enable + start；使用 || true 防止单元文件缺失时整个脚本退出
sudo systemctl enable --now "$SERVICE_NAME" 2>/dev/null || true

# ---- 6. 输出部署摘要 ----
printf '===== PureMark Ubuntu 部署完成 =====\n'
printf '调度入口（app.py）：%s\n' "$TARGET_DIR/app.py"
printf '前端 UI 页面：       %s\n' "$HTML_DIR"
printf 'Nginx 配置文件：     %s\n' "$NGINX_AVAILABLE"
printf 'Nginx 启用链接：     %s\n' "$NGINX_ENABLED"
printf 'systemd 服务：       %s （已启用并启动）\n' "$SYSTEMD_DEST"
printf '\n验证命令：\n'
printf '  systemctl status %s\n' "$SERVICE_NAME"
printf '  curl http://127.0.0.1:5000/health\n'
printf '  curl https://md.sergget.qzz.io/health\n'
printf '=====================================\n'
