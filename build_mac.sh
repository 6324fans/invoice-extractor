#!/bin/bash
# Mac App 打包脚本
# 用法: bash build_mac.sh
set -e
cd "$(dirname "$0")"

echo ">>> 预编译 OCR 二进制（Vision）"
python3 -c "import ocr; ocr.ensure_ocr_binary()"

echo ">>> PyInstaller 打包 .app"
pyinstaller --noconfirm --windowed \
  --name "发票提取" \
  --add-data "wechat_ocr.swift:." \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --hidden-import "webview.platforms.edgechromium" \
  --hidden-import "webview.platforms.cocoa" \
  main.py

echo ""
echo ">>> 完成: dist/发票提取.app"
echo "双击运行，或命令行: open 'dist/发票提取.app'"
