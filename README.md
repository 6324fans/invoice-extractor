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
**已全部内包，用户免安装：**
- **macOS**：用系统 Vision(OCR) + Quartz(PDF渲染) + sips/textutil，无需任何外部工具，app 开箱即用
- **Windows**：Tesseract-OCR(含中文包 chi_sim/chi_tra) 与 poppler(pdftotext/pdftoppm 及其 dll) 均打进 exe，开箱即用

> 开发模式(跑源码)下：Mac 需 `pip install pyobjc`；Windows 需装 Tesseract+poppler。

## 打包

### Mac App（本地）
```bash
bash build_mac.sh
# 产出 dist/发票提取.app
```

## 下载安装包（开箱即用）

前往 [Releases 页面](https://github.com/6324fans/invoice-extractor/releases) 下载对应平台的压缩包，解压后直接运行：

- **Windows**：`invoice-extractor-windows.zip` → 解压 → 运行 `invoice-extractor.exe`
- **macOS**：`invoice-extractor-mac.zip` → 解压得 `invoice-extractor.app` → 双击运行
  （首次打开若提示"无法验证开发者"，右键 → 打开）

> 打 tag `v*`（如 `v1.0.0`）会触发 GitHub Actions 自动构建并发布 Release。
> 也可在 Actions 页面手动 Run workflow（产物在对应运行的 Artifacts 里）。

## 数据位置
打包运行后，数据库与图片存于：
- macOS/Windows：`~/.invoice-extractor/`（invoices.db、data/）

## 说明
- 微信 4.0 原始图片为 AES 加密 `.dat`，无法直接读取；仅处理已解码的缓存/附件。
- Windows 的 `.exe` 必须在 Windows 环境（CI）构建，PyInstaller 不支持交叉编译。
