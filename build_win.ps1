# Windows 本地构建脚本（与 .github/workflows/build.yml 一致，含 pythonnet 修复）。
# 用法：
#   1) 先装好 Tesseract 并把 tesseract.exe + 依赖 dll 放到 bundle_bin\，
#      chi_sim/chi_tra/eng.traineddata 放到 bundle_tessdata\（与 CI 相同的 staging）。
#   2) pip install -r requirements.txt pyinstaller
#   3) pwsh build_win.ps1
#
# 产物：dist\invoice-extractor\invoice-extractor.exe
#
# 关键修复点：加 --collect-all pythonnet / clr_loader / webview。
# 原打包命令只有 --hidden-import webview.platforms.edgechromium，pythonnet 与
# clr_loader 被零散收集，冻结后 webview.start() 报
# "Failed to resolve Python.Runtime.Loader.Initialize"，窗口打不开。

$ErrorActionPreference = "Stop"

if (-not (Test-Path "bundle_bin\tesseract.exe")) {
    Write-Warning "未找到 bundle_bin\tesseract.exe —— 请先按脚本头部说明 staging Tesseract。"
}

pyinstaller --noconfirm --windowed --name invoice-extractor `
  --add-data "templates;templates" `
  --add-data "static;static" `
  --add-data "bundle_bin;bin" `
  --add-data "bundle_tessdata;tessdata" `
  --collect-all fitz `
  --collect-all pythonnet `
  --collect-all clr_loader `
  --collect-all webview `
  --hidden-import webview.platforms.edgechromium `
  --hidden-import pytesseract `
  main.py

Write-Host "构建完成: dist\invoice-extractor\invoice-extractor.exe"
