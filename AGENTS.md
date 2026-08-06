# PureMark Converter Agent Guide

This document outlines essential project-specific information for OpenCode agents to work efficiently in the `puremark-converter` repository.

## 1. Project Architecture & Components

*   **Distributed System**: Comprises two main services:
    *   `main_server/` (Ubuntu): Primary server, handles scheduling, health checks, format routing, and external APIs.
    *   `ocr_server/` (Windows): Dedicated OCR service for image and scanned PDF conversions.
*   **Smart Routing**:
    *   Office documents (`.docx`, `.xlsx`, `.pptx`) are processed by `MarkItDown` locally on the Ubuntu `main_server`.
    *   Images and scanned PDFs are forwarded to the Windows `ocr_server` for high-accuracy OCR.
*   **Failover & Local OCR Degradation**:
    *   Ubuntu `main_server` actively monitors the Windows `ocr_server` status via `/health` (checking `win11_status`).
    *   If the Windows `ocr_server` is offline or unreachable, the Ubuntu `main_server` automatically degrades to using a locally-running, lazily-loaded `PaddleOCR` (CPU mode) for images and scanned PDFs. This local OCR uses `_local_ocr_exec_lock` for concurrency safety.
*   **OCR Mode**: The Windows `ocr_server` **always uses `paddlepaddle-cpu`**. The GTX 960 GPU is **not utilized** due to architecture incompatibility (Maxwell CC 5.2 not supported by PaddlePaddle wheels). Do not attempt to enable GPU acceleration unless the hardware and PaddlePaddle versions explicitly support it.

## 2. Developer Commands

*   **Ubuntu Main Server Deployment**:
    ```bash
    sudo bash ./deploy_ubuntu.sh
    ```
*   **Windows OCR Server Deployment**:
    ```powershell
    powershell -ExecutionPolicy Bypass -File .\deploy_windows.ps1
    ```
*   **Manual Server Start (for development/debugging)**:
    *   **Ubuntu Main Server**:
        ```bash
        cd main_server
        python3 app.py
        ```
    *   **Windows OCR Server**:
        ```powershell
        cd ocr_server
        python ocr_server.py
        ```

## 3. API & Conventions

*   **Health Check Endpoint**: `GET /health` (default port 5000, configurable via `PORT` environment variable).
    *   Returns detailed status, including `win11_status` on the Ubuntu server.
*   **Node Identification**: Use environment variables `NODE_NAME` (e.g., `ubuntu`, `win11`) and `NODE_ROLE` (e.g., `light`, `heavy`).
*   **Error Response Format**: `{"success": false, "error": "..."}` with appropriate HTTP status codes.
*   **CORS**: Currently set to `origins: "*"`. In production, this should be restricted to specific domains (e.g., `https://sergget.qzz.io`).
*   **Layout Reconstruction**: The Windows OCR pipeline performs geometric-heuristic layout reconstruction on its output for images and scanned PDFs. This process populates fields like `layout_reconstructed` (boolean) and `content_raw` (original raw text before reconstruction).

## 4. Environment & Dependencies

*   **Python `numpy` Version**: `numpy<2.0.0` is required due to `PaddlePaddle` linkage.
*   **Windows Services**: The Windows `ocr_server` is deployed as an NSSM Windows Service. This is safe because `PaddleOCR` runs in CPU-only mode, avoiding Session 0 isolation issues with GPU contexts.

## 5. Other Key Information

*   The project structure splits code for Ubuntu and Windows into `main_server/` and `ocr_server/` respectively.
*   Refer to `分布式文档转换系统_实施计划与测试路线.md` for a comprehensive overview of project phases, technical decisions, and testing notes.