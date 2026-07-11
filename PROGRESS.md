# 分布式文档转换系统 — Win11 端进度（简化版）

> 最后更新：2026-07-11
> 完整技术决策/接口契约见：`分布式文档转换系统_实施计划与测试路线.md`

## 当前状态

阶段 3（Win11 OCR 服务）已完成并端到端验证通过，NSSM 服务化部署已就绪。阶段 4 调度层由 Ubuntu 端主导实现，已完成（详见 Ubuntu 侧进度）。

## 关键结论

- **GTX 960（Maxwell CC 5.2）GPU 加速永久不可用**：PaddlePaddle 预编译 wheel 最低支持 sm_61(Pascal)，与 CUDA 驱动版本无关（`nvidia-smi` 13.0 vs `nvcc` 11.8 只是驱动/工具链版本差异，非冲突）。
- **已切换到 `paddlepaddle-cpu`（零 CUDA 依赖）**：不再加载 `cudart64_118.dll`，代码硬编码 `GPU_AVAILABLE=True, GPU_USABLE=False, OCR_MODE="cpu"`，移除了原先约 100 行的 GPU 动态检测代码。
- **部署方式变更为 NSSM Windows 服务**：因为 CPU 版零 CUDA 依赖，Session 0 隔离问题不再存在，NSSM 现在安全（此前因 rclone+WinFsp 踩坑而定的"必须用任务计划程序"原则，在纯 CPU 场景下已不适用；若未来换 Pascal+ 显卡重新启用 GPU，需重新评估退回任务计划程序）。
- 中文 OCR 识别准确率 100%（Ubuntu 端验证）。

## 环境速查

```
项目路径:     E:\Code\markitdown_selfhosted\
虚拟环境:     .venv\ (Python 3.11.9，因 PaddlePaddle 2.6.2 不支持 3.12+)
PaddlePaddle: 2.6.2 CPU 版（零 CUDA 依赖）
NVIDIA 驱动:  580.97（仅用于显示输出，OCR 不使用）
GPU:          GTX 960 2GB, Maxwell CC 5.2 — 永久 CPU 模式
CUDA Toolkit: 可安全卸载
部署方式:     NSSM 服务 Win11OCRService
```

## 关键配置参数（环境变量）

| 变量 | 默认值 | 说明 |
|---|---|---|
| NODE_NAME | win11 | 节点标识 |
| NODE_ROLE | heavy | 节点角色 |
| PORT | 5000 | 服务端口 |
| OCR_MAX_FILE_MB | 100 | 上传文件上限 |
| OCR_MAX_PDF_PAGES | 200 | PDF 页数上限 |
| OCR_TIMEOUT_SEC | 300 | 单次 OCR 超时 |
| OCR_PDF_DPI | 200 | PDF 渲染分辨率 |

## NSSM 常用命令

```powershell
nssm status Win11OCRService      # 查看状态
nssm restart Win11OCRService     # 重启
nssm stop / start Win11OCRService
nssm edit Win11OCRService        # 修改配置（GUI）
nssm remove Win11OCRService      # 删除（需先 stop）
# 日志：E:\Code\markitdown_selfhosted\nssm_stdout.log / nssm_stderr.log
```

一键部署：右键 `install_nssm_service.bat` → 以管理员身份运行。
备选方案（无 NSSM）：任务计划程序「登录时启动」，见实施计划文件阶段 3 章节。

## 待处理

- [ ] 锁屏状态下服务是否正常（NSSM 以用户账户运行，理论安全，建议实测）
- [ ] 手动关闭服务，验证健康检查立刻反映 DOWN
- [ ] 性能基准数据采集：不同分辨率/页数图片的 OCR 耗时，供调度权重参考
- [ ] 可选：新增 `GET /benchmark` 端点

## 代码结构

```
E:\Code\markitdown_selfhosted\
├── .venv/                          ← Python 3.11 虚拟环境
├── ocr_server.py                   ← Win11 OCR 服务（paddlepaddle-cpu）
├── install_nssm_service.bat        ← NSSM 一键部署脚本
├── start_ocr_service.bat           ← 命令行启动脚本（备用）
├── requirements_win11.txt          ← Win11 专用依赖
├── requirements_win11_frozen.txt   ← 精确版本快照
├── requirements.txt                ← 通用依赖（参考）
├── README.md
├── PROGRESS.md                     ← 本文件
└── 分布式文档转换系统_实施计划与测试路线.md
```

> `ubuntu/` 目录已移除，Ubuntu 侧代码独立仓库维护。
