#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PureMark 本地测试服务器一键启动与管理脚本

该脚本用于在本地一键拉起分布式文档转换系统的两个核心服务：
1. main_server (Ubuntu 调度主服务，默认端口 8000)
2. ocr_server  (Windows OCR 服务，默认端口 8001)

并自动配置相关的环境变量（例如将主服务的 WIN11_OCR_URL 指向本地的 OCR 服务）。
支持统一的控制台日志输出管理、优雅的 Ctrl+C 一键退出（自动清理子进程），防止端口占用。
"""

import os
import sys
import time
import subprocess
import threading
import signal

# =============================================================================
# 配置参数
# =============================================================================
MAIN_SERVER_PORT = 8000
OCR_SERVER_PORT = 8001

# 获取 Python 解释器路径：优先使用本仓库虚拟环境里的解释器（其中装齐了
# pdfplumber / fitz / markitdown / python-docx / flask 等依赖），否则回退到
# 当前 sys.executable。这样无论用系统 python 还是 .venv 的 python 启动本脚本，
# 拉起的主/OCR 服务都会使用同一套已装好依赖的解释器。
_PYTHON_EXE = sys.executable
_venv_python = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".venv", "Scripts", "python.exe")
if os.path.isfile(_venv_python):
    PYTHON_EXE = _venv_python
else:
    PYTHON_EXE = _PYTHON_EXE

def log_stream(stream, prefix, color_code=None):
    """
    读取子进程的输出，并加上特定前缀打印到控制台。
    """
    # 颜色代码，用于区分不同服务的日志
    # Color codes: 32 = Green (Main), 36 = Cyan (OCR), 0 = Reset
    start_color = f"\033[{color_code}m" if color_code else ""
    end_color = "\033[0m" if color_code else ""

    try:
        # 当使用 text=True 时，stream 读出的是 str，不再需要 b''
        for line in iter(stream.readline, ''):
            if not line:
                break
            decoded_line = line.rstrip()
            print(f"{start_color}{prefix}{end_color} {decoded_line}", flush=True)
    except Exception as e:
        print(f"[{prefix} Log Error] {e}", flush=True)
    finally:
        try:
            stream.close()
        except Exception:
            pass

def main():
    print("=" * 60)
    print("               PureMark 本地测试服务器启动工具")
    print("=" * 60)
    print(f"使用 Python 解释器: {PYTHON_EXE}")
    print(f"[主服务端口]       : {MAIN_SERVER_PORT}")
    print(f"[OCR 服务端口]      : {OCR_SERVER_PORT}")
    print("=" * 60)

    # 1. 准备环境变量
    main_env = os.environ.copy()
    main_env["NODE_NAME"] = "ubuntu-local"
    main_env["NODE_ROLE"] = "light"
    main_env["PORT"] = str(MAIN_SERVER_PORT)
    main_env["WIN11_OCR_URL"] = f"http://127.0.0.1:{OCR_SERVER_PORT}"
    
    ocr_env = os.environ.copy()
    ocr_env["NODE_NAME"] = "win11-local"
    ocr_env["NODE_ROLE"] = "heavy"
    ocr_env["PORT"] = str(OCR_SERVER_PORT)

    processes = []
    threads = []

    # 定义清理函数
    def cleanup_processes():
        print("\n" + "=" * 60)
        print("正在关闭所有测试服务器进程...")
        print("=" * 60)
        for proc in processes:
            if proc.poll() is None:
                try:
                    print(f"正在终止进程 PID: {proc.pid}...")
                    proc.terminate()
                except Exception as e:
                    print(f"终止进程 PID {proc.pid} 失败: {e}")
        
        # 等待所有进程退出
        for proc in processes:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    print(f"进程 PID {proc.pid} 未能在规定时间退出，强制杀死 (kill)...")
                    proc.kill()
                except Exception as e:
                    print(f"杀死进程 PID {proc.pid} 失败: {e}")
            except Exception:
                pass
        print("所有子进程已清理完毕。")

    # 注册退出信号处理
    def signal_handler(sig, frame):
        cleanup_processes()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        # 2. 启动 OCR Server
        print("[1/2] 正在拉起 ocr_server (Windows OCR 节点)...")
        # 检查 ocr_server 路径
        ocr_script = os.path.join("ocr_server", "ocr_server.py")
        if not os.path.exists(ocr_script):
            print(f"[错误] 未找到 OCR 服务启动脚本: {ocr_script}")
            sys.exit(1)

        # Python 子进程在 Windows 上由于 buffering=1 与二进制管道冲突会有 Warning，我们可以通过设置 universal_newlines=True (或 text=True) 来解决。
        ocr_proc = subprocess.Popen(
            [PYTHON_EXE, ocr_script, "--port", str(OCR_SERVER_PORT)],
            env=ocr_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1
        )
        processes.append(ocr_proc)

        # 启动 OCR 服务的日志读取线程
        t_ocr_out = threading.Thread(target=log_stream, args=(ocr_proc.stdout, "[OCR_Server]", "36"), daemon=True)
        t_ocr_err = threading.Thread(target=log_stream, args=(ocr_proc.stderr, "[OCR_Server_ERR]", "31"), daemon=True)
        t_ocr_out.start()
        t_ocr_err.start()
        threads.extend([t_ocr_out, t_ocr_err])

        # 稍微等待，让 OCR 服务初始化
        print("等待 2 秒以初始化 OCR 服务...")
        time.sleep(2)

        # 3. 启动 Main Server
        print("[2/2] 正在拉起 main_server (调度主服务)...")
        main_script = os.path.join("main_server", "app.py")
        if not os.path.exists(main_script):
            print(f"[错误] 未找到主服务启动脚本: {main_script}")
            cleanup_processes()
            sys.exit(1)

        main_proc = subprocess.Popen(
            [PYTHON_EXE, main_script],
            env=main_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1
        )
        processes.append(main_proc)

        # 启动主服务的日志读取线程
        t_main_out = threading.Thread(target=log_stream, args=(main_proc.stdout, "[Main_Server]", "32"), daemon=True)
        t_main_err = threading.Thread(target=log_stream, args=(main_proc.stderr, "[Main_Server_ERR]", "31"), daemon=True)
        t_main_out.start()
        t_main_err.start()
        threads.extend([t_main_out, t_main_err])

        print("\n" + "=" * 60)
        print(f" 所有服务已拉起！")
        print(f" -> 调度主服务地址: http://127.0.0.1:{MAIN_SERVER_PORT}")
        print(f" -> OCR 服务地址  : http://127.0.0.1:{OCR_SERVER_PORT}")
        print(" 提示：按 Ctrl+C 可以一键关闭所有服务并退出。")
        print("=" * 60 + "\n")

        # 4. 主线程循环监控子进程状态
        while True:
            # 检查是否有子进程已经退出
            ocr_status = ocr_proc.poll()
            main_status = main_proc.poll()

            if ocr_status is not None:
                print(f"\n[警告] OCR 服务意外退出，退出码: {ocr_status}")
                break
            if main_status is not None:
                print(f"\n[警告] 主服务意外退出，退出码: {main_status}")
                break

            time.sleep(1)

    except KeyboardInterrupt:
        pass
    finally:
        cleanup_processes()

if __name__ == "__main__":
    # 确保支持 Windows 控制台颜色输出
    if sys.platform == "win32":
        os.system("")
    main()
