# PureMark Self-Hosted

这是一个面向 Ubuntu 主节点 + Windows OCR 节点的分布式文档转换系统，适合后续通过部署脚本分别在两台节点上拉起服务。应用名已统一为 PureMark。

## 项目结构

- main_server/: Ubuntu 侧主服务，负责调度、健康检查、格式路由和对外接口。
- ocr_server/: Windows 侧 OCR 服务，负责图像/PDF OCR 转换。

## 根目录文档

- 分布式文档转换系统_实施计划与测试路线.md：项目整体计划、接口约定和技术决策。
- README.md：当前项目入口说明。

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

后续也可直接手动启动：

```bash
# Ubuntu
cd /path/to/main_server
python3 app.py

# Windows
cd E:\Code\markitdown_selfhosted\ocr_server
python ocr_server.py
```
