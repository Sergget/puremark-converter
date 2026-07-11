nssm install Win11OCRService "E:\Code\markitdown_selfhosted\.venv\Scripts\python.exe" "ocr_server.py"
nssm set Win11OCRService AppDirectory "E:\Code\markitdown_selfhosted"
nssm set Win11OCRService AppEnvironmentExtra "NODE_NAME=win11" "NODE_ROLE=heavy" "PORT=5000" "OCR_MAX_FILE_MB=100"  "OCR_MAX_PDF_PAGES=200" "OCR_TIMEOUT_SEC=300" "OCR_PDF_DPI=200"
nssm set Win11OCRService Start SERVICE_AUTO_START
nssm set Win11OCRService AppExit Default Restart
nssm set Win11OCRService AppStdout "E:\Code\markitdown_selfhosted\nssm_stdout.log"
nssm set Win11OCRService AppStderr "E:\Code\markitdown_selfhosted\nssm_stderr.log"
nssm set Win11OCRService AppStdoutCreationDisposition 4
nssm set Win11OCRService AppStderrCreationDisposition 4
nssm set Win11OCRService AppRotateFiles 1
nssm set Win11OCRService AppRotateSeconds 86400
nssm set Win11OCRService DisplayName "Win11 OCR Service (PaddleOCR CPU)"
nssm set Win11OCRService Description "Win11 OCR Node - PaddleOCR CPU, port 5000"
nssm start Win11OCRService