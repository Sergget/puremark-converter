import subprocess
import time
import urllib.request
import json
import sys
import os

def test_servers():
    print("============================================================")
    print("             正在测试本地联合服务器连通性")
    print("============================================================")
    
    # 1. 启动本地联合服务器
    python_exe = sys.executable
    print(f"正在拉起 run_local_servers.py，使用解释器: {python_exe}...")
    
    proc = subprocess.Popen([python_exe, "run_local_servers.py"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # 等待 5 秒，让两个服务全部完全启动
    print("等待 5 秒让服务完全拉起并完成初始化...")
    time.sleep(5)
    
    try:
        # 2. 检查 main_server 是否在 8000 端口正常运行
        print("检查调度主服务 (http://127.0.0.1:8000/health)...")
        with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as r:
            assert r.status == 200
            data = json.loads(r.read().decode('utf-8'))
            print("主服务健康检查响应:")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            
            # 验证主服务应该已经感知到本地 OCR 服务
            # 并且 win11_status (代表 Windows OCR 服务的状态) 应该是在线/正常 (UP)
            assert data.get("status") == "UP"
            win11_status = data.get("win11_status")
            print(f"检测到的 OCR 服务状态: {win11_status}")
            assert win11_status == "UP", "本地 OCR 服务应在线且可达 (UP)"

        # 3. 检查 ocr_server 是否在 8001 端口正常运行
        print("\n检查 OCR 服务 (http://127.0.0.1:8001/health)...")
        with urllib.request.urlopen("http://127.0.0.1:8001/health", timeout=5) as r:
            assert r.status == 200
            ocr_data = json.loads(r.read().decode('utf-8'))
            print("OCR 服务健康检查响应:")
            print(json.dumps(ocr_data, indent=2, ensure_ascii=False))
            assert ocr_data.get("status") == "UP"

        print("\n============================================================")
        print("🎉 测试通过！本地主服务器和 OCR 服务均拉起成功，且主服务正常感知并连通 OCR 服务！")
        print("============================================================")
        
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        # 输出子进程可能产生的报错信息
        print("尝试读取服务器退出或报错日志...")
        # 强制非阻塞读取一些数据
        proc.terminate()
        out, err = proc.communicate(timeout=3)
        print(f"STDOUT:\n{out.decode('utf-8', errors='replace')}")
        print(f"STDERR:\n{err.decode('utf-8', errors='replace')}")
        sys.exit(1)
    finally:
        print("正在终止本地服务进程...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
            print("子进程已完全退出。")
        except subprocess.TimeoutExpired:
            proc.kill()
            print("强制杀死子进程。")

if __name__ == "__main__":
    test_servers()
