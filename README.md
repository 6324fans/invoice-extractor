# 微信发票提取

从微信本地数据中按日期提取发票（图片 / PDF / 压缩包 / Word），OCR 识别并入库展示，支持桌面窗口应用。

## 功能
- 指定一个大路径，递归搜索其下所有子目录（不依赖微信目录结构 / wxid 命名）
- 按日期范围筛选文件，提取发票
- 支持格式：图片(png/jpg/heic/webp)、压缩包(zip/rar/7z)、Word(docx/doc)、PDF
- 发票字段提取：类型、代码、号码、日期、购销方、金额、价税合计、校验码
- 列表展示 + 按时间筛选 + 关键字搜索 + 删除
- 跨平台：macOS 用 Vision OCR，Windows 用 Tesseract OCR

## 运行（开发）
```bash
pip install -r requirements.txt
python3 main.py          # 桌面窗口
# 或仅后端：python3 app.py  → http://127.0.0.1:5000
```

## 系统依赖
- **macOS**：自带 Vision/sips/textutil；PDF 需 `brew install poppler`
- **Windows**：需安装
  - Tesseract-OCR（含中文语言包 chi_sim/chi_tra）→ 设置 `TESSERACT_CMD` 或加入 PATH
  - poppler（pdftotext/pdftoppm）→ 加入 PATH

## 打包

### Mac App（本地）
```bash
bash build_mac.sh
# 产出 dist/发票提取.app
```

### Windows exe（CI 自动构建）
推送到 GitHub 后，Actions 在 windows-latest 上自动构建 `.exe`，
从仓库 **Actions → 对应运行 → Artifacts** 下载 `invoice-extractor-windows`。

也可手动触发：Actions 页面 → Run workflow。

## 数据位置
打包运行后，数据库与图片存于：
- macOS/Windows：`~/.invoice-extractor/`（invoices.db、data/）

## 说明
- 微信 4.0 原始图片为 AES 加密 `.dat`，无法直接读取；仅处理已解码的缓存/附件。
- Windows 的 `.exe` 必须在 Windows 环境（CI）构建，PyInstaller 不支持交叉编译。
