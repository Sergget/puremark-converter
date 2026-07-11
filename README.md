# 阶段 1：健康检查服务 — 部署说明

两端跑的是同一份 `health_server.py`，只是启动时的环境变量和常驻方式不同。

## 通用准备（两端都要做）

```bash
pip install flask flask-cors psutil --break-system-packages   # Ubuntu
pip install flask flask-cors psutil                            # Win11（不用加 --break-system-packages）
```

把 `health_server.py` 拷贝到两台机器上即可，不需要改代码，只改启动时的环境变量。

---

## Ubuntu Server 部署（systemd 常驻）

1. 把 `health_server.py` 放到 `/home/sergio7/health_check/health_server.py`
2. 把 `ubuntu/health-check.service` 拷贝到 `/etc/systemd/system/health-check.service`
3. 启动：
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now health-check.service
   sudo systemctl status health-check.service
   ```
4. 验证：
   ```bash
   curl http://127.0.0.1:5000/health
   ```

后续如果要接 Cloudflare Tunnel 暴露到公网看板轮询，在 `config.yml` 里加一条 `service: http://127.0.0.1:5000` 的 ingress 规则即可，和你现有的 WordPress/Immich 配置方式一致。

---

## Win11 部署（登录会话自启，**不用** NSSM/Windows 服务模式）

这里刻意不用 NSSM 注册成 Windows 服务，是因为服务运行在 Session 0，等阶段 3 部署 PaddleOCR GPU 服务时，Session 0 里的进程大概率拿不到桌面会话的 GPU 上下文（和你之前 rclone + WinFsp 遇到的问题是同一类）。健康检查本身虽然不需要 GPU，但为了和阶段 3 的常驻方式保持一致（复用同一个"开机登录自启"的任务），这里先用同样的方式搭好。

**用任务计划程序（Task Scheduler）配置"登录时启动"：**

1. 把 `health_server.py` 放到比如 `C:\services\health_check\health_server.py`
2. 打开"任务计划程序" → 创建任务（不是"基本任务"，用完整的"创建任务"）：
   - **常规**：勾选"不管用户是否登录都要运行" → 不要勾这个，**改为勾选"只在用户登录时运行"**（这样才能拿到桌面会话，未来 GPU 任务才不会踩 Session 0 的坑）；勾选"使用最高权限运行"
   - **触发器**：新建 → "登录时"，选择当前用户账户
   - **操作**：新建 → 启动程序
     - 程序：`C:\Windows\System32\cmd.exe`
     - 参数：`/c set NODE_NAME=win11&& set NODE_ROLE=heavy&& set PORT=5000&& python C:\services\health_check\health_server.py`
   - **条件**：取消勾选"只有在使用交流电源时才启动此任务"（笔记本才需要关心，台式机忽略）
   - **设置**：取消"如果任务运行超过以下时间..."的限制（默认可能是 3 天自动结束，改成"不限制"）
3. 保存后手动登录一次账户测试（或右键任务 → 运行），验证：
   ```powershell
   curl http://127.0.0.1:5000/health
   ```

**验证开机顺序**：重启一次 Win11，正常登录桌面后，等 10-20 秒再 curl 一次，确认服务确实是登录后自动起来的，而不是要手动点开。

---

## 联调验证清单（对应文档中的阶段 1 测试）

在 Ubuntu 上执行，验证两端互通：

```bash
curl http://192.168.0.24:5000/health     # 本机
curl http://192.168.0.81:5000/health     # Win11

# 模拟调度层的超时探测行为
curl --max-time 2 http://192.168.0.81:5000/health

# 验证 CORS（模拟看板跨域请求）
curl -I -H "Origin: https://sergget.qzz.io" http://192.168.0.24:5000/health | grep -i access-control
```

关掉 Win11 上的任务（或直接注销登录），再跑一次探测：

```bash
curl --max-time 2 http://192.168.0.81:5000/health
```

应该在 2 秒内超时失败 —— 这就是阶段 4 调度层判定 Win11 为 DOWN 的探测行为原型，可以直接照这个逻辑写路由判断。
