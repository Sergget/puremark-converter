# PureMark Self-Hosted

这是一个面向 Ubuntu 主节点 + Windows OCR 节点的分布式文档转换系统，适合后续通过部署脚本分别在两台节点上拉起服务。应用名已统一为 PureMark。

## 项目结构

- main_server/: Ubuntu 侧主服务，负责调度、健康检查、格式路由和对外接口。
- ocr_server/: Windows 侧 OCR 服务，负责图像/PDF OCR 转换。

## 根目录文档

- 分布式文档转换系统_实施计划与测试路线.md：项目整体计划、接口约定和技术决策。
- README.md：当前项目入口说明。

## 系统特性

- **智能格式路由**：办公文档（.docx/.xlsx/.pptx 等）走 MarkItDown 本地快速提取，图片与扫描型 PDF 转发 Win11 节点做高精度 OCR 识别。
- **高可用容灾**：Ubuntu 节点实时监测 Win11 OCR 节点状态（`/health` 端点输出 `win11_status`）。当 Win11 离线或不可达时，Ubuntu 主服务会自动降级为本地 PaddleOCR（CPU 模式，延迟单例加载）进行处理。
- **几何启发式版面重建**：基于 OCR 返回的行级 bbox 自动识别标题级别（# / ## / ###）、段落分割和缩进引用，还原文档层次结构。
- **可视化裁剪与多格式导出**：前端支持可视化区域选择裁剪，并支持导出为 Markdown (.md)、纯文本 (.txt) 或标准格式 Word (.docx) 文档。

## 部署说明

- Ubuntu 主服务目录：main_server
- Windows OCR 服务目录：ocr_server
- Ubuntu 部署脚本：deploy_ubuntu.sh
- Windows 部署脚本：deploy_windows.ps1

### Ubuntu 部署

```bash
sudo bash ./deploy_ubuntu.sh
```

部署后，应用文件会同步到：
- /var/www/markitdown/app.py
- /var/www/markitdown/html/

### Windows 部署

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1
```

### 本地测试服务器（一键联调启动）

为了方便开发和本地测试，我们在项目根目录下提供了一键拉起本地联调环境的 Python 脚本。它将自动在本地启动 `main_server`（主调度服务，默认 8000 端口）和 `ocr_server`（Windows OCR 服务，默认 8001 端口），并做好环境变量绑定和日志合并输出，支持一键优雅关闭：

```bash
# 激活你的项目虚拟环境后，在根目录下执行：
python run_local_servers.py
```

- **主调度服务地址**：`http://127.0.0.1:8000`
- **OCR 服务地址**：`http://127.0.0.1:8001`
- **一键退出**：在终端按 `Ctrl + C`，脚本会自动优雅地关闭、清理全部子进程，避免端口占用。

此外，你还可以通过以下脚本测试本地联合服务是否能够互通（探活机制测试）：
```bash
python test_local_servers.py
```

### 传统手动分立启动

后续也可直接手动独立拉起各个服务：

```bash
# Ubuntu (主服务)
cd main_server
python3 app.py

# Windows (OCR 服务)
cd ocr_server
python ocr_server.py
```
