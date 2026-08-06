import subprocess
import time
import requests
import sys
import os

def run_tests():
    # 运行临时 OCR 服务器
    env = os.environ.copy()
    env["PORT"] = "5055"

    print("Starting temporary OCR server on port 5055...")
    python_executable = os.path.join(".venv", "Scripts", "python.exe")
    if not os.path.exists(python_executable):
        python_executable = sys.executable
    proc = subprocess.Popen([python_executable, "ocr_server/ocr_server.py", "--port", "5055"], env=env)

    # 给服务器 3 秒时间初始化
    time.sleep(3)

    try:
        print("Testing GET /health metrics...")
        r = requests.get("http://127.0.0.1:5055/health")
        assert r.status_code == 200
        data = r.json()
        print("Initial health state:", data)
        assert "metrics" in data
        metrics = data["metrics"]
        assert metrics["total_success_count"] == 0
        assert metrics["total_fail_count"] == 0
        assert metrics["total_elapsed_ms"] == 0
        assert metrics["average_elapsed_ms"] == 0.0

        print("Sending invalid conversion request (will fail in _do_ocr processing after task_started)...")
        # 传一个名义上是 PNG 但实际完全损坏的字节流，诱导 PIL 在 _do_ocr 中抛出异常
        files = {"file": ("test.png", b"corrupted_png_bytes_that_will_throw_exception")}
        r = requests.post("http://127.0.0.1:5055/convert", files=files)
        # 会在 _do_ocr 内部由于 PIL 打不开而报错 500
        assert r.status_code == 500
        assert r.json()["success"] is False
        assert "processing failed" in r.json()["error"]

        print("Checking health metrics after 1 failed conversion...")
        r = requests.get("http://127.0.0.1:5055/health")
        data = r.json()
        metrics = data["metrics"]
        print("Metrics after 1 fail:", metrics)
        assert metrics["total_success_count"] == 0
        assert metrics["total_fail_count"] == 1
        assert metrics["total_elapsed_ms"] > 0
        assert metrics["average_elapsed_ms"] == 0.0

        print("\nALL METRIC INTEGRATION TESTS ON OCR SERVER PASSED SUCCESSFULLY!")
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        print("Terminating server...")
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    run_tests()
