"""
Win11 重型 OCR 服务 —— 阶段 3
合并健康检查 + PaddleOCR CPU 文档转换，一个 Flask 进程跑全部。

启动方式（命令行测试）：
    set NODE_NAME=win11&& set NODE_ROLE=heavy&& set PORT=5000&& python ocr_server.py

启动方式（开机自启 — NSSM Windows 服务，推荐）：
    见 PROGRESS.md → NSSM 部署步骤

依赖安装（一次性）：
    pip install -r requirements_win11.txt

前置条件：
    - paddlepaddle==2.6.2 (CPU 版本)，无需 CUDA Toolkit
    - GTX 960 (Maxwell CC 5.2) 永久使用 CPU 模式
    - Session 0 (Windows 服务) 完全安全 — CPU 版本不加载 CUDA DLL
"""

import io
import os
import sys
import time
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

from flask import Flask, request, jsonify
from flask_cors import CORS
import psutil


def _force_utf8_stdio():
    """Ensure stdout/stderr use UTF-8 so Chinese logs are written correctly on Windows."""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name)
        try:
            if getattr(stream, "encoding", None):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

# =============================================================================
# 配置 —— 全部来自环境变量，和 Ubuntu 端保持一致
# =============================================================================

NODE_NAME = os.environ.get("NODE_NAME", "win11")
NODE_ROLE = os.environ.get("NODE_ROLE", "heavy")
PORT = int(os.environ.get("PORT", 5000))

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

# CORS：和现有健康检查保持一致，生产环境建议收紧为看板域名
CORS(app, resources={
    r"/health":             {"origins": "*"},
    r"/health/gpu":         {"origins": "*"},
    r"/convert":            {"origins": "*"},
    r"/supported_formats":  {"origins": "*"},
    r"/":                   {"origins": "*"},
})

# =============================================================================
# 任务计数器 —— 供调度层判断节点繁忙程度
# =============================================================================

_lock = threading.Lock()
_active_tasks = 0


def task_started():
    global _active_tasks
    with _lock:
        _active_tasks += 1


def task_finished():
    global _active_tasks
    with _lock:
        _active_tasks = max(0, _active_tasks - 1)


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

            print("[ocr_server] 正在初始化 PaddleOCR（首次加载模型，CPU 模式）...")
            print("[ocr_server]   模型: PP-OCRv4 mobile 系列")
            print("[ocr_server]   语言: ch（中文）")
            print("[ocr_server]   方向分类: 关闭（use_angle_cls=False，节省内存）")
            print("[ocr_server]   设备: CPU（GTX 960 Maxwell CC 5.2 不支持 GPU 加速）")

            _ocr_instance = PaddleOCR(
                lang="ch",
                use_angle_cls=False,       # 关闭方向分类，节省内存
                use_gpu=False,              # GTX 960 永久 CPU 模式
            )

            print("[ocr_server] PaddleOCR 初始化完成（CPU 模式）")
            return _ocr_instance

        except Exception as e:
            _ocr_init_error = str(e)
            print(f"[ocr_server] PaddleOCR 初始化失败: {e}")
            traceback.print_exc()
            return None


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
    except Exception:
        pass

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
    except Exception:
        pass

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
    except Exception:
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
        return jsonify({"success": False, "error": "No file part in the request"}), 400

    file = request.files["file"]
    if file.filename is None or file.filename == "":
        return jsonify({"success": False, "error": "No file selected"}), 400

    # ---- 2. 读取文件到内存（不落盘）----
    file_bytes = file.read()
    file_size_mb = len(file_bytes) / (1024 * 1024)

    if file_size_mb > MAX_FILE_SIZE_MB:
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
        return jsonify({
            "success": False,
            "error": f"Unsupported format '{ext}'. Supported: {sorted(IMAGE_EXTENSIONS | PDF_EXTENSIONS)}"
        }), 400

    # ---- 4. 获取 OCR 引擎 ----
    ocr = get_ocr()
    if ocr is None:
        return jsonify({
            "success": False,
            "error": f"OCR engine not available. "
                     f"Init error: {_ocr_init_error or 'unknown'}"
        }), 503

    # ---- 5. 执行 OCR（带超时保护 + 任务计数）----
    task_started()
    t_start = time.time()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_ocr, file_bytes, ext, file_type, file.filename)
            try:
                result = future.result(timeout=OCR_TIMEOUT_SEC)
            except FutureTimeoutError:
                return jsonify({
                    "success": False,
                    "error": f"OCR timed out after {OCR_TIMEOUT_SEC}s. "
                             f"The file may be too large or complex."
                }), 504
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"OCR processing failed: {str(e)}"
        }), 500
    finally:
        task_finished()

    elapsed_ms = round((time.time() - t_start) * 1000)

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

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        page_count = doc.page_count

        if page_count > MAX_PDF_PAGES:
            doc.close()
            raise ValueError(
                f"PDF has {page_count} pages, exceeds max {MAX_PDF_PAGES}"
            )

        all_texts = []
        all_lines = []
        for page_num in range(page_count):
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
        except (IndexError, TypeError, ValueError):
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
    print("=" * 60)
    print(f"  Win11 重型 OCR 服务")
    print(f"  节点: {NODE_NAME}  角色: {NODE_ROLE}  端口: {PORT}")
    print(f"  引擎: PaddlePaddle 2.6.2 CPU（paddlepaddle-cpu）")
    print(f"  OCR 模式: CPU（永久 — GTX 960 Maxwell CC 5.2）")
    print("=" * 60)

    # 显示 GPU 基本信息（仅展示用）
    gpu_info = _get_gpu_display_info()
    if gpu_info.get("gpu_name"):
        print(f"[ocr_server] GPU: {gpu_info['gpu_name']} | "
              f"显存: {gpu_info.get('gpu_memory_mb', '?')} MB | "
              f"驱动: {gpu_info.get('driver_version', '?')}")
        print("[ocr_server] GPU 加速: 不可用（Maxwell CC 5.2 < 需要 CC 6.1+ Pascal）")
    else:
        print("[ocr_server] GPU: 未检测到 nvidia-smi（不影响服务）")
    print("[ocr_server] CUDA Toolkit: 无需安装（paddlepaddle-cpu 不依赖 CUDA）")

    print(f"[ocr_server] /convert 端点将在首次请求时延迟加载 OCR 模型")
    print(f"[ocr_server] /supported_formats 端点已就绪（供调度器动态发现）")
    print(f"[ocr_server] 服务启动，监听 0.0.0.0:{PORT} ...")
    print("=" * 60)

    # debug=False：常驻服务不要开 debug（reloader 在 Windows 上行为不一致）
    # threaded=False：确保单线程串行处理，避免多个 OCR 任务同时吃内存
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=False)
