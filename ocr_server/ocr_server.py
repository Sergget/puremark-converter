import io
import os
import sys
import time
import threading
import traceback
import logging  # Import logging module
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError


def _force_utf8_stdio():
    """Ensure stdout/stderr use UTF-8 so Chinese logs are written correctly on Windows."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        try:
            if getattr(stream, "encoding", None):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def ensure_project_venv():
    """Fail fast if ocr_server.py is run outside the project virtual environment."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    expected_python = os.path.join(project_root, ".venv", "Scripts", "python.exe")
    if os.path.exists(expected_python):
        current_python = os.path.abspath(sys.executable)
        if os.path.normcase(current_python) != os.path.normcase(os.path.abspath(expected_python)):
            logging.error("[ocr_server] 错误：当前 Python 解释器不是项目虚拟环境中的 .venv。")
            logging.error(f"[ocr_server] 当前解释器: {current_python}")
            logging.error(f"[ocr_server] 请改用: {expected_python} {os.path.join(project_root, 'ocr_server.py')}")
            logging.error("[ocr_server] 例如：.\\venv\\Scripts\\python.exe .\\ocr_server.py")
            sys.exit(1)


_force_utf8_stdio()
ensure_project_venv()

from flask import Flask, request, jsonify
from flask_cors import CORS
import psutil

# =============================================================================
# 配置 —— 全部来自环境变量，和 Ubuntu 端保持一致
# =============================================================================

NODE_NAME = os.environ.get("NODE_NAME", "win11")
NODE_ROLE = os.environ.get("NODE_ROLE", "heavy")
PORT = int(os.environ.get("PORT", 5000))

# 从命令行参数获取端口，如果命令行参数未提供，则使用环境变量或默认值
# 这是为了方便在不修改服务配置的情况下临时更改端口进行测试
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description="OCR Server")
    parser.add_argument(
        '--port', type=int, default=PORT, help='Listening port for the server'
    )
    args = parser.parse_args()
    PORT = args.port
    logging.info(f"Effective PORT after parsing arguments: {PORT}")

# 业务参数（可通过环境变量覆盖，不设就是内置默认值）
MAX_FILE_SIZE_MB = int(os.environ.get("OCR_MAX_FILE_MB", 100))       # 文件大小上限
MAX_PDF_PAGES = int(os.environ.get("OCR_MAX_PDF_PAGES", 200))        # PDF 页数上限
OCR_TIMEOUT_SEC = int(os.environ.get("OCR_TIMEOUT_SEC", 300))        # 单次 OCR 超时
PDF_RENDER_DPI = int(os.environ.get("OCR_PDF_DPI", 200))             # PDF 渲染 DPI

# 支持的图片后缀（这些格式 PaddleOCR 直接识别）
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}
# 需要逐页渲染后 OCR 的文档后缀
PDF_EXTENSIONS = {".pdf"}

START_TIME = time.time()

# =============================================================================
# GPU / OCR 模式 —— 硬编码说明
# =============================================================================
# 2026-07-11: 切换到 paddlepaddle==2.6.2 (CPU 版本)，移除所有 GPU/CUDA 依赖。
#
# 原因：GTX 960 是 Maxwell 架构 (Compute Capability 5.2)，PaddlePaddle GPU
# wheel 只包含 sm_61 (Pascal) 及以上架构的 kernel。升级 GPU 前 GPU 加速不可用。
#
# paddlepaddle-cpu 的优势：
#   - 不加载任何 CUDA DLL → Session 0 (Windows 服务/NSSM) 完全安全
#   - CUDA Toolkit 11.8 已卸载，节省磁盘空间
#   - 功能与识别精度与 GPU 版本完全一致（仅速度不同）
#
# 如果未来升级到 Pascal+ 显卡（如 GTX 1060）：
#   1. pip install paddlepaddle-gpu==2.6.2（替换 CPU 版本）
#   2. 将 use_gpu 改为 True
#   3. 安装对应版本的 CUDA Toolkit

GPU_AVAILABLE = True   # nvidia-smi 可见 GPU（仅显示输出用）
GPU_USABLE = False     # Maxwell CC 5.2，GPU 加速不可用
OCR_MODE = "cpu"       # 永久 CPU 模式

# =============================================================================
# Flask 应用
# =============================================================================

app = Flask(__name__)

# Configure logging for the Flask app
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
app.logger.setLevel(logging.INFO)

# CORS：和现有健康检查保持一致，生产环境建议收紧为看板域名
CORS(app, resources={
    r"/health":             {"origins": "*"},
    r"/health/gpu":         {"origins": "*"},
    r"/convert":            {"origins": "*"},
    r"/supported_formats":  {"origins": "*"},
    r"/":                   {"origins": "*"},
})

# =============================================================================
# 任务计数器与指标 —— 供调度层及看板展示
# =============================================================================

_lock = threading.Lock()
_active_tasks = 0
_total_success_count = 0
_total_fail_count = 0
_total_elapsed_ms = 0


def task_started():
    global _active_tasks
    with _lock:
        _active_tasks += 1


def task_finished():
    global _active_tasks
    with _lock:
        _active_tasks = max(0, _active_tasks - 1)


def record_conversion_metrics(success: bool, elapsed_ms: float):
    global _total_success_count, _total_fail_count, _total_elapsed_ms
    with _lock:
        if success:
            _total_success_count += 1
        else:
            _total_fail_count += 1
        _total_elapsed_ms += elapsed_ms


# =============================================================================
# PaddleOCR 延迟初始化（单例，CPU 模式）
# =============================================================================
# 不在 import 时加载模型 —— 首次 /convert 请求触发初始化，避免常驻吃内存。
# 加载后保持常驻（不卸载），后续请求直接复用。

_ocr_instance = None
_ocr_init_lock = threading.Lock()
_ocr_init_error = None     # 记录初始化失败的原因，后续请求直接返回错误


def get_ocr():
    """
    获取 PaddleOCR 单例。首次调用时初始化模型（会下载模型文件）。
    GTX 960 架构不兼容 GPU，永久使用 CPU 模式（use_gpu=False）。
    初始化失败时会记录错误，后续调用直接复用 None，避免反复尝试。
    """
    global _ocr_instance, _ocr_init_error

    if _ocr_instance is not None:
        return _ocr_instance

    if _ocr_init_error is not None:
        return None

    with _ocr_init_lock:
        # 双重检查：拿到锁后再次确认
        if _ocr_instance is not None:
            return _ocr_instance
        if _ocr_init_error is not None:
            return None

        try:
            from paddleocr import PaddleOCR

            app.logger.info("[ocr_server] 正在初始化 PaddleOCR（首次加载模型，CPU 模式）...")
            app.logger.info("[ocr_server]   模型: PP-OCRv4 mobile 系列")
            app.logger.info("[ocr_server]   语言: ch（中文）")
            app.logger.info("[ocr_server]   方向分类: 关闭（use_angle_cls=False，节省内存）")
            app.logger.info("[ocr_server]   设备: CPU（GTX 960 Maxwell CC 5.2 不支持 GPU 加速）")

            _ocr_instance = PaddleOCR(
                lang="ch",
                use_angle_cls=False,       # 关闭方向分类，节省内存
                use_gpu=False,              # GTX 960 永久 CPU 模式
            )

            app.logger.info("[ocr_server] PaddleOCR 初始化完成（CPU 模式）")
            return _ocr_instance

        except Exception as e:
            _ocr_init_error = str(e)
            app.logger.error(f"[ocr_server] PaddleOCR 初始化失败: {e}")
            app.logger.error(traceback.format_exc())
            return None

# Global ThreadPoolExecutor for OCR tasks
ocr_executor = ThreadPoolExecutor(max_workers=1)

# =============================================================================
# 辅助：获取 GPU 基本信息（仅用于 /health/gpu 展示，不用于兼容性判断）
# =============================================================================

def _get_gpu_display_info():
    """
    通过 nvidia-smi 获取 GPU 基本信息（型号、显存、驱动版本）。
    仅用于 /health/gpu 端点展示，不参与任何兼容性判断逻辑。
    兼容性结论已硬编码（GPU_USABLE=False，原因见文件顶部注释）。
    """
    info = {
        "gpu_name": None,
        "gpu_memory_mb": None,
        "driver_version": None,
    }

    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) >= 2:
                info["gpu_name"] = parts[0]
                info["gpu_memory_mb"] = int(float(parts[1]))
    except Exception as e:
        app.logger.warning(f"Failed to get GPU name/memory from nvidia-smi: {e}")

    # 驱动版本
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            info["driver_version"] = result.stdout.strip()
    except Exception as e:
        app.logger.warning(f"Failed to get GPU driver version from nvidia-smi: {e}")

    return info


# =============================================================================
# 端点：GET /health —— 健康检查
# =============================================================================

@app.route("/health", methods=["GET"])
def health():
    cpu_percent = psutil.cpu_percent(interval=0.2)
    mem = psutil.virtual_memory()

    with _lock:
        active = _active_tasks
        success_cnt = _total_success_count
        fail_cnt = _total_fail_count
        elapsed_tot = _total_elapsed_ms

    # 计算平均耗时
    avg_elapsed_ms = round(elapsed_tot / success_cnt, 1) if success_cnt > 0 else 0.0

    return jsonify({
        "status": "UP",
        "node": NODE_NAME,
        "role": NODE_ROLE,
        "cpu": round(cpu_percent / 100, 4),
        "mem": round(mem.percent / 100, 4),
        "mem_available_mb": round(mem.available / 1024 / 1024, 1),
        "active_tasks": active,
        "uptime_seconds": round(time.time() - START_TIME, 1),
        # v1.1 新增字段（与 Ubuntu 侧对齐）
        "gpu_available": GPU_AVAILABLE,
        "gpu_usable": GPU_USABLE,
        "ocr_mode": OCR_MODE,
        "metrics": {
            "total_success_count": success_cnt,
            "total_fail_count": fail_cnt,
            "total_elapsed_ms": elapsed_tot,
            "average_elapsed_ms": avg_elapsed_ms
        }
    }), 200


# =============================================================================
# 端点：GET /health/gpu —— GPU 详细状态
# =============================================================================

@app.route("/health/gpu", methods=["GET"])
def health_gpu():
    # GPU 基本信息（型号、显存、驱动）
    info = _get_gpu_display_info()

    # GPU 兼容性（硬编码，原因见文件顶部注释）
    info["gpu_available"] = GPU_AVAILABLE
    info["gpu_usable"] = GPU_USABLE
    info["gpu_arch_warning"] = (
        "GTX 960 是 Maxwell 架构 (CC 5.2)，PaddlePaddle 2.6.2 预编译 wheel "
        "只支持 sm_61 (Pascal) 及以上。GPU 加速永久不可用，OCR 运行在 CPU 模式。"
    )

    # 实时显存使用
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = [p.strip() for p in result.stdout.strip().split(",")]
            if len(parts) >= 3:
                info["vram_used_mb"] = int(float(parts[0]))
                info["vram_free_mb"] = int(float(parts[1]))
                info["gpu_util_pct"] = int(float(parts[2]))
    except Exception as e:
        app.logger.warning(f"Failed to get GPU memory/utilization from nvidia-smi: {e}")
        info["vram_used_mb"] = None
        info["vram_free_mb"] = None
        info["gpu_util_pct"] = None

    # OCR 引擎状态
    info["ocr_loaded"] = _ocr_instance is not None
    info["ocr_error"] = _ocr_init_error
    info["ocr_using_gpu"] = False  # 永久 CPU

    return jsonify(info), 200


# =============================================================================
# 端点：GET /supported_formats —— 节点能力自描述（v1.1 新增）
# =============================================================================
# 供调度器动态发现节点能力，避免硬编码格式列表到调度器代码中。

@app.route("/supported_formats", methods=["GET"])
def supported_formats():
    return jsonify({
        "node": NODE_NAME,
        "role": NODE_ROLE,
        "engine": "paddleocr_cpu",
        "formats": sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS),
        "max_file_size_mb": MAX_FILE_SIZE_MB,
        "max_pdf_pages": MAX_PDF_PAGES,
        "ocr_mode": OCR_MODE,
        "capabilities": {
            "image_ocr": True,       # 图片直接 OCR
            "pdf_render_ocr": True,  # PDF 渲染 → OCR（扫描件）
            "office_formats": False, # docx/xlsx 等由 Ubuntu markitdown 处理
            "structured_lines": True,  # /convert 可返回逐行 bbox+置信度（见 include_lines 参数）
        },
    }), 200


# =============================================================================
# 端点：POST /convert —— 文档 OCR 转换
# =============================================================================

@app.route("/convert", methods=["POST"])
def convert_document():
    # ---- 0. 可选查询参数 ----
    # include_lines=false/0/no 可关闭行级结构化数据（省响应体积/带宽），默认开启。
    include_lines = request.args.get("include_lines", "true").strip().lower() not in (
        "false", "0", "no"
    )

    # ---- 1. 参数校验 ----
    if "file" not in request.files:
        app.logger.warning("No file part in the request")
        return jsonify({"success": False, "error": "No file part in the request"}), 400

    file = request.files["file"]
    if file.filename is None or file.filename == "":
        app.logger.warning("No file selected")
        return jsonify({"success": False, "error": "No file selected"}), 400

    # ---- 2. 读取文件到内存（不落盘）----
    file_bytes = file.read()
    file_size_mb = len(file_bytes) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
        app.logger.warning(
            f"File too large ({file_size_mb:.1f}MB). Max allowed: {MAX_FILE_SIZE_MB}MB"
        )
        return jsonify({
            "success": False,
            "error": f"File too large ({file_size_mb:.1f}MB). Max allowed: {MAX_FILE_SIZE_MB}MB"
        }), 413

    # ---- 3. 判断文件类型 ----
    _, ext = os.path.splitext(file.filename)
    ext = ext.lower()

    if ext in IMAGE_EXTENSIONS:
        file_type = "image"
    elif ext in PDF_EXTENSIONS:
        file_type = "pdf"
    else:
        app.logger.warning(
            f"Unsupported format '{ext}'. Supported: {sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS)}"
        )
        return jsonify({
            "success": False,
            "error": f"Unsupported format '{ext}'. Supported: {sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS)}"
        }), 400

    # ---- 4. 获取 OCR 引擎 ----
    ocr = get_ocr()
    if ocr is None:
        app.logger.error(f"OCR engine not available. Init error: {_ocr_init_error or 'unknown'}")
        return jsonify({
            "success": False,
            "error": f"OCR engine not available. "
                     f"Init error: {_ocr_init_error or 'unknown'}"
        }), 503

    # ---- 5. 执行 OCR（带超时保护 + 任务计数）----
    task_started()
    t_start = time.time()

    try:
        future = ocr_executor.submit(_do_ocr, file_bytes, ext, file_type, file.filename)
        try:
            result = future.result(timeout=OCR_TIMEOUT_SEC)
        except FutureTimeoutError:
            elapsed_ms = round((time.time() - t_start) * 1000)
            record_conversion_metrics(False, elapsed_ms)
            app.logger.error(
                f"OCR timed out after {OCR_TIMEOUT_SEC}s for file {file.filename}. "
                f"The file may be too large or complex."
            )
            return jsonify({
                "success": False,
                "error": f"OCR timed out after {OCR_TIMEOUT_SEC}s. "
                         f"The file may be too large or complex."
            }), 504
    except Exception as e:
        elapsed_ms = round((time.time() - t_start) * 1000)
        record_conversion_metrics(False, elapsed_ms)
        app.logger.error(f"OCR processing failed for file {file.filename}: {str(e)}")
        app.logger.error(traceback.format_exc())
        return jsonify({
            "success": False,
            "error": f"OCR processing failed: {str(e)}"
        }), 500
    finally:
        task_finished()

    elapsed_ms = round((time.time() - t_start) * 1000)
    record_conversion_metrics(True, elapsed_ms)
    app.logger.info(
        f"Successfully processed file {file.filename} ({file_size_mb:.1f}MB, "
        f"pages: {result['pages']}) in {elapsed_ms}ms"
    )

    # ---- 6. 返回结果 ----
    # success/content/engine/pages/elapsed_ms 语义与字段名保持不变（下游健康检查/
    # 降级逻辑依赖这些字段）。lines 是新增的可选字段，仅在 include_lines=true
    # （默认）时附带，不影响老调用方。
    response_body = {
        "success": True,
        "filename": file.filename,
        "content": result["text"],
        "engine": "paddleocr_cpu",
        "pages": result["pages"],
        "elapsed_ms": elapsed_ms,
    }
    if include_lines:
        response_body["lines"] = result["lines"]

    return jsonify(response_body), 200


# =============================================================================
# OCR 核心逻辑（在独立线程中执行，受超时保护）
# =============================================================================

def _do_ocr(file_bytes, ext, file_type, original_filename):
    """
    根据文件类型分派处理：
      - 图片：直接送 PaddleOCR
      - PDF：PyMuPDF 逐页渲染 → 逐页 OCR → 拼接文本
    全程在内存中流转，不落盘。
    """
    ocr = get_ocr()
    if ocr is None:
        raise RuntimeError("OCR engine is not initialized")

    if file_type == "image":
        # 图片：PIL 解码 → numpy array → PaddleOCR
        from PIL import Image
        import numpy as np

        app.logger.info(f"Processing image: {original_filename}")
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        img_array = np.array(img)

        raw_result = ocr.ocr(img_array, cls=False)
        # 单页图片不需要 page 字段（下游只有多页 PDF 场景才需要区分页码）
        page_lines = _extract_lines(raw_result)
        text = _lines_to_text(page_lines)
        pages = 1
        all_lines = page_lines

    elif file_type == "pdf":
        # PDF：PyMuPDF 逐页渲染
        import fitz  # PyMuPDF

        app.logger.info(f"Processing PDF: {original_filename}")
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = doc.page_count

        if page_count > MAX_PDF_PAGES:
            doc.close()
            app.logger.warning(
                f"PDF {original_filename} has {page_count} pages, "
                f"exceeds max {MAX_PDF_PAGES}"
            )
            raise ValueError(
                f"PDF has {page_count} pages, exceeds max {MAX_PDF_PAGES}"
            )

        all_texts = []
        all_lines = []
        for page_num in range(page_count):
            app.logger.debug(f"Rendering page {page_num + 1}/{page_count} of {original_filename}")
            page = doc[page_num]
            # 渲染为 PNG 图片（内存中）
            pix = page.get_pixmap(dpi=PDF_RENDER_DPI)
            img_bytes = pix.tobytes("png")

            # PIL 解码 → numpy array → PaddleOCR
            from PIL import Image
            import numpy as np
            img = Image.open(io.BytesIO(img_bytes))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img_array = np.array(img)

            raw_result = ocr.ocr(img_array, cls=False)
            # 多页场景：每行标上 1-indexed 页码，方便下游按页重建版面
            page_lines = _extract_lines(raw_result, page_num=page_num + 1)
            page_text = _lines_to_text(page_lines)

            if page_text.strip():
                all_texts.append(f"--- 第 {page_num + 1} 页 ---\n{page_text}")
            all_lines.extend(page_lines)

        doc.close()
        text = "\n\n".join(all_texts)
        pages = page_count
        app.logger.info(f"Finished processing PDF: {original_filename} with {pages} pages")

    return {"text": text, "pages": pages, "lines": all_lines}


def _extract_lines(ocr_result, page_num=None):
    """
    从 PaddleOCR 的原始输出中提取结构化行信息（文字 + bbox 坐标 + 置信度）。

    PaddleOCR 2.7 返回格式：
    [
        [   # 第一张图的识别结果
            [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ('识别文字', confidence)],
            ...
        ]
    ]
    如果没有检测到文字，可能返回 [None] 或 [[]]。

    返回 list[dict]，每个 dict 形如：
        {"text": "识别出的文字", "bbox": [[x1,y1],...,[x4,y4]], "confidence": 0.98}
    若传入 page_num，则每个 dict 额外带上 "page": page_num（1-indexed），
    用于多页 PDF 场景下游按页区分行。bbox 坐标是渲染图片的像素坐标，不做归一化。
    """
    if ocr_result is None:
        return []

    # ocr_result 是一个 list，每个元素对应一张输入图
    if len(ocr_result) == 0:
        return []

    page_result = ocr_result[0]

    if page_result is None:
        return []

    lines = []
    for item in page_result:
        if item is None:
            continue
        try:
            # item = [bbox_list, (text, confidence)]
            bbox_raw, (text, confidence) = item
            if not text or not text.strip():
                continue

            # numpy 类型不能直接 json.dumps，统一转成原生 float
            bbox = [[round(float(x), 1), round(float(y), 1)] for x, y in bbox_raw]

            line = {
                "text": text,
                "bbox": bbox,
                "confidence": round(float(confidence), 4),
            }
            if page_num is not None:
                line["page"] = page_num
            lines.append(line)
        except (IndexError, TypeError, ValueError) as e:
            app.logger.debug(f"Error parsing OCR line item: {item}. Error: {e}")
            continue

    return lines


def _lines_to_text(lines):
    """把结构化行按识别顺序拼接成一段文本（content 字段沿用的老行为）。"""
    return "\n".join(line["text"] for line in lines)


# =============================================================================
# 端点：GET / —— 服务说明
# =============================================================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "service": "Win11 Heavy OCR Service (阶段 3)",
        "node": NODE_NAME,
        "role": NODE_ROLE,
        "endpoints": {
            "/health":             "GET  — 健康检查（调度层探活）",
            "/health/gpu":         "GET  — GPU 详细状态",
            "/supported_formats":  "GET  — 节点能力自描述（调度器动态发现）",
            "/convert":            "POST — OCR 文档转换（multipart/form-data, field: file）",
        },
        "supported_formats": sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS),
        "engine": "PaddleOCR CPU (PP-OCRv4 mobile, GTX 960 CC 5.2 不支持 GPU)",
    }), 200


# =============================================================================
# 启动入口
# =============================================================================

if __name__ == "__main__":
    app.logger.info("=" * 60)
    app.logger.info(f"  Win11 重型 OCR 服务")
    app.logger.info(f"  节点: {NODE_NAME}  角色: {NODE_ROLE}  端口: {PORT}")
    app.logger.info(f"  引擎: PaddlePaddle 2.6.2 CPU（paddlepaddle-cpu）")
    app.logger.info(f"  OCR 模式: CPU（永久 — GTX 960 Maxwell CC 5.2）")
    app.logger.info("=" * 60)

    # 显示 GPU 基本信息（仅展示用）
    gpu_info = _get_gpu_display_info()
    if gpu_info.get("gpu_name"):
        app.logger.info(f"  GPU: {gpu_info['gpu_name']} | "
              f"显存: {gpu_info.get('gpu_memory_mb', '?')} MB | "
              f"驱动: {gpu_info.get('driver_version', '?')}")
        app.logger.info("  GPU 加速: 不可用（Maxwell CC 5.2 < 需要 CC 6.1+ Pascal）")
    else:
        app.logger.info("  GPU: 未检测到 nvidia-smi（不影响服务）")
    app.logger.info("  CUDA Toolkit: 无需安装（paddlepaddle-cpu 不依赖 CUDA）")

    app.logger.info(f"  /convert 端点将在首次请求时延迟加载 OCR 模型")
    app.logger.info(f"  /supported_formats 端点已就绪（供调度器动态发现）")
    app.logger.info(f"服务启动，监听 0.0.0.0:{PORT} ...")
    app.logger.info("=" * 60)

    # debug=False：常驻服务不要开 debug（reloader 在 Windows 上行为不一致）
    # threaded=False：确保单线程串行处理，避免多个 OCR 任务同时吃内存
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=False)
