"""按日期扫描 + 解包 zip/docx/pdf/webp/heic，产出可 OCR 的图片路径。

跨平台：macOS 用 sips/textutil；Windows 用 Pillow/python-docx。
"""
import os
import re
import sys
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timedelta

IS_MAC = sys.platform == "darwin"

if IS_MAC:
    DEFAULT_WX_ROOT = os.path.expanduser(
        "~/Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
    )
else:
    # Windows 微信 4.0 默认路径
    DEFAULT_WX_ROOT = os.path.join(
        os.environ.get("USERPROFILE", os.path.expanduser("~")),
        "Documents", "xwechat_files"
    )

# 直接可读的图片扩展名（NSImage 可解）
PLAIN_IMG_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff"}
# 需要 sips 转换的扩展名
CONVERT_EXT = {".heic", ".heif", ".webp"}
# 压缩包扩展名
ARCHIVE_EXT = {".zip", ".rar", ".7z"}
# Word 扩展名
WORD_EXT = {".docx", ".doc"}
# 所有要搜索的发票候选格式：图片 + 压缩包 + Word + PDF
TARGET_EXTS = PLAIN_IMG_EXT | CONVERT_EXT | ARCHIVE_EXT | WORD_EXT | {".pdf"}


def _bundled_bin_dir():
    """返回内包工具(poppler等)所在目录，打包后优先用程序旁的 bin/。"""
    candidates = []
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "bin"))
        candidates.append(os.path.join(getattr(sys, "_MEIPASS", ""), "bin"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def _find_tool(name):
    """查找外部工具：环境变量 > 内包 bin/ > PATH。Windows 自动加 .exe。"""
    env_key = {"pdftotext": "PDFTOTEXT_CMD",
               "pdftoppm": "PDFTOPPM_CMD"}.get(name)
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    bin_dir = _bundled_bin_dir()
    if bin_dir:
        cand = os.path.join(bin_dir, name + (".exe" if not IS_MAC else ""))
        if os.path.exists(cand):
            return cand
    return shutil.which(name)

# 路径中包含这些片段的视为无关缓存，跳过
SKIP_SUBSTR = ["/video/", "WeAppIcon", "/Avatar/", "/Stickers/", "OpenImResource",
               "/__MACOSX/", "/.Trash/", "/Backup/", "/all_users/"]


def _is_user_data_dir(path):
    """判断是否为微信用户数据目录：同时含 msg/ temp/ cache/ 三特征目录。

    不依赖目录名（wxid_* 可能被重命名），靠结构特征识别。
    """
    if not os.path.isdir(path):
        return False
    return (os.path.isdir(os.path.join(path, "msg"))
            and os.path.isdir(os.path.join(path, "temp"))
            and os.path.isdir(os.path.join(path, "cache")))


def detect_wx_user_dir(root=None):
    """在微信根目录下探测用户数据目录（不依赖目录名，靠结构特征）。

    若只有一个匹配目录直接返回；多个则返回最近修改的那个（当前在用账号）。
    返回路径或 None。
    """
    root = root or DEFAULT_WX_ROOT
    if not os.path.isdir(root):
        return None
    candidates = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if _is_user_data_dir(full):
            candidates.append(full)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # 多账号：优先选有实际数据的目录（msg 下有文件），
    # 再按最近修改排序（当前活跃账号）
    def score(p):
        msg_dir = os.path.join(p, "msg")
        has_data = 0
        try:
            if os.path.isdir(msg_dir) and any(os.scandir(msg_dir)):
                has_data = 1
        except OSError:
            pass
        return (has_data, os.path.getmtime(p))
    candidates.sort(key=score, reverse=True)
    return candidates[0]


def _in_range(mtime, start, end):
    """mtime 为 datetime，判断是否在 [start, end] 闭区间（按天）。"""
    return start.date() <= mtime.date() <= end.date()


def _should_skip(path):
    return any(s in path for s in SKIP_SUBSTR)


def _kind_of(ext):
    if ext in PLAIN_IMG_EXT or ext in CONVERT_EXT:
        return "image"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in WORD_EXT:
        return "word"
    if ext == ".pdf":
        return "pdf"
    return None


def iter_candidate_files(base_path, start, end):
    """从指定大路径递归搜索所有子目录，按日期筛选发票候选文件。

    支持任意大路径（微信根目录、用户目录、或任意父目录均可）。
    不依赖微信目录结构，纯按文件扩展名识别：
      图片(png/jpg/...)、压缩包(zip)、Word(docx)、PDF

    返回: (path, kind, mtime_datetime)
    kind: 'image' | 'archive' | 'word' | 'pdf'
    """
    if not os.path.isdir(base_path):
        return []

    results = []
    seen_paths = set()
    for root, dirs, files in os.walk(base_path):
        # 跳过无关目录（不进入）
        if _should_skip(root):
            dirs[:] = []
            continue
        for fn in files:
            p = os.path.join(root, fn)
            ext = os.path.splitext(fn)[1].lower()
            kind = _kind_of(ext)
            if not kind:
                continue
            if p in seen_paths:
                continue
            try:
                mt = datetime.fromtimestamp(os.path.getmtime(p))
            except OSError:
                continue
            if not _in_range(mt, start, end):
                continue
            # 跳过微信加密 .dat（虽扩展名不在目标集，但保险）
            if ext == ".dat":
                continue
            seen_paths.add(p)
            results.append((p, kind, mt))
    return results


def _convert_to_png(src, dst_png):
    """把 heic/webp 等转成 png。macOS 用 sips，其他平台用 Pillow。"""
    if IS_MAC:
        try:
            r = subprocess.run(
                ["sips", "-s", "format", "png", src, "--out", dst_png],
                capture_output=True, timeout=30,
            )
            return r.returncode == 0 and os.path.exists(dst_png)
        except Exception:
            return False
    # Windows / Linux：Pillow
    try:
        from PIL import Image
        Image.open(src).save(dst_png, "PNG")
        return os.path.exists(dst_png)
    except Exception:
        return False


# 兼容旧调用名
_sips_convert = _convert_to_png


def _is_image_ext(name):
    ext = os.path.splitext(name)[1].lower()
    return ext in PLAIN_IMG_EXT or ext in CONVERT_EXT


def extract_images(path, workdir):
    """把一个候选文件解包成可直接 OCR 的图片路径列表。

    返回: list[str]（图片绝对路径，可能位于 workdir 临时目录）
    - 普通图片: 直接返回（heic/webp 转换后）
    - zip: 解压后递归找图片
    - docx: 取 word/media/* 图片
    - pdf: 优先 pdftotext 文字层（返回特殊标记由调用方处理）；否则 sips 渲染首页
    """
    ext = os.path.splitext(path)[1].lower()
    out = []

    if ext in PLAIN_IMG_EXT:
        out.append(path)
    elif ext in CONVERT_EXT:
        dst = os.path.join(workdir, os.path.splitext(os.path.basename(path))[0] + ".png")
        if _sips_convert(path, dst):
            out.append(dst)
    elif ext == ".zip":
        out.extend(_extract_zip(path, workdir))
    elif ext in (".rar", ".7z"):
        # 需 unrar/7z，未安装时优雅跳过（已安装则尝试）
        out.extend(_extract_archive_cmd(path, workdir))
    elif ext in WORD_EXT:
        # docx 取内嵌图；doc 用 textutil 提取文字
        out.extend(_extract_word(path, workdir))
    elif ext == ".pdf":
        out.extend(_extract_pdf(path, workdir))

    return [p for p in out if p and os.path.exists(p)]


def _extract_zip(path, workdir):
    sub = os.path.join(workdir, "zip_" + _safe_name(os.path.basename(path)))
    os.makedirs(sub, exist_ok=True)
    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(sub)
    except Exception:
        return []
    # 递归找图片；同时处理嵌套 docx/pdf（最多一层）
    imgs = []
    for root, _dirs, files in os.walk(sub):
        # 跳过 macosx 杂项
        if "__MACOSX" in root:
            continue
        for fn in files:
            p = os.path.join(root, fn)
            e = os.path.splitext(fn)[1].lower()
            if e in PLAIN_IMG_EXT:
                imgs.append(p)
            elif e in CONVERT_EXT:
                dst = os.path.join(root, os.path.splitext(fn)[0] + ".png")
                if _sips_convert(p, dst):
                    imgs.append(dst)
            elif e in (".docx", ".pdf"):
                imgs.extend(extract_images(p, workdir))
    return imgs


def _extract_word(path, workdir):
    """Word 文档：docx 取 word/media/ 内嵌图 + 文字；doc 用 textutil 提取文字。

    返回的图片/文字标记由调用方识别（.pdftext 后缀表示纯文字）。
    """
    ext = os.path.splitext(path)[1].lower()
    out = []

    if ext == ".docx":
        # 取内嵌图片
        sub = os.path.join(workdir, "docx_" + _safe_name(os.path.basename(path)))
        os.makedirs(sub, exist_ok=True)
        try:
            with zipfile.ZipFile(path) as zf:
                for name in zf.namelist():
                    if name.startswith("word/media/") and _is_image_ext(os.path.basename(name)):
                        target = os.path.join(sub, os.path.basename(name))
                        with zf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())
        except Exception:
            pass
        for fn in os.listdir(sub):
            p = os.path.join(sub, fn)
            e = os.path.splitext(fn)[1].lower()
            if e in PLAIN_IMG_EXT:
                out.append(p)
            elif e in CONVERT_EXT:
                dst = os.path.join(sub, os.path.splitext(fn)[0] + ".png")
                if _sips_convert(p, dst):
                    out.append(dst)

    # docx 与 doc 都用 textutil 提取正文文字（发票可能在正文里）
    txt_marker = _textutil_to_text(path, workdir)
    if txt_marker:
        out.insert(0, txt_marker)  # 文字在前（字段更准，无需 OCR）
    return out


def _textutil_to_text(path, workdir):
    """提取 Word 正文文字。macOS 用 textutil，其他平台用 python-docx。

    返回 .pdftext 标记路径或 None。
    """
    txt = _word_to_text(path)
    if not txt or len(txt) < 20:
        return None
    txt_dst = os.path.join(workdir, _safe_name(os.path.basename(path)) + "_text.txt")
    marker = txt_dst + ".pdftext"
    open(marker, "w", encoding="utf-8").write(txt)
    return marker


def _word_to_text(path):
    """从 doc/docx 提取正文文字。macOS 用 textutil，否则 python-docx(仅docx)。"""
    ext = os.path.splitext(path)[1].lower()
    if IS_MAC:
        textutil = shutil.which("textutil")
        if textutil:
            tmp = tempfile.NamedTemporaryFile(suffix=".txt", delete=False, dir=path + "_x" and None)
            tmp.close()
            try:
                subprocess.run([textutil, "-convert", "txt", "-encoding", "UTF-8",
                                "-output", tmp.name, path],
                               capture_output=True, timeout=30)
                return open(tmp.name, "r", encoding="utf-8", errors="ignore").read().strip()
            except Exception:
                return ""
            finally:
                try:
                    os.unlink(tmp.name)
                except OSError:
                    pass
    # Windows / Linux：python-docx（仅支持 docx）
    if ext == ".docx":
        try:
            import docx
            doc = docx.Document(path)
            return "\n".join(p.text for p in doc.paragraphs).strip()
        except Exception:
            return ""
    return ""
    return marker


def _extract_archive_cmd(path, workdir):
    """rar/7z 压缩包：用 unrar/7z 解压后递归找图片。未安装则返回空。"""
    ext = os.path.splitext(path)[1].lower()
    tool = None
    if ext == ".rar":
        tool = shutil.which("unrar")
    elif ext == ".7z":
        tool = shutil.which("7z") or shutil.which("7zz")
    if not tool:
        return []
    sub = os.path.join(workdir, "arch_" + _safe_name(os.path.basename(path)))
    os.makedirs(sub, exist_ok=True)
    try:
        if ext == ".rar":
            subprocess.run([tool, "x", "-y", "-o" + sub, path],
                           capture_output=True, timeout=120)
        else:
            subprocess.run([tool, "x", "-y", f"-o{sub}", path],
                           capture_output=True, timeout=120)
    except Exception:
        return []
    imgs = []
    for root, _dirs, files in os.walk(sub):
        if "__MACOSX" in root:
            continue
        for fn in files:
            p = os.path.join(root, fn)
            e = os.path.splitext(fn)[1].lower()
            if e in PLAIN_IMG_EXT:
                imgs.append(p)
            elif e in CONVERT_EXT:
                dst = os.path.join(root, os.path.splitext(fn)[0] + ".png")
                if _sips_convert(p, dst):
                    imgs.append(dst)
            elif e in (".docx", ".doc", ".pdf", ".zip"):
                imgs.extend(extract_images(p, workdir))
    return imgs


def _render_pdf_quartz(path, workdir, safe, max_pages=4):
    """macOS 用 Quartz 框架渲染 PDF 页面为 png（无需外部 poppler，打包友好）。"""
    try:
        import Quartz
        from Foundation import NSURL
    except Exception:
        return []
    url = NSURL.fileURLWithPath_(path)
    doc = Quartz.CGPDFDocumentCreateWithURL(url)
    if not doc:
        return []
    n = min(Quartz.CGPDFDocumentGetNumberOfPages(doc), max_pages)
    imgs = []
    for i in range(1, n + 1):
        page = Quartz.CGPDFDocumentGetPage(doc, i)
        if not page:
            continue
        rect = Quartz.CGPDFPageGetBoxRect(page, Quartz.kCGPDFMediaBox)
        # 2x 缩放足够清晰
        scale = 2.0
        w = int(rect.size.width * scale)
        h = int(rect.size.height * scale)
        cs = Quartz.CGColorSpaceCreateDeviceRGB()
        ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, 0, cs,
                                           Quartz.kCGImageAlphaPremultipliedLast)
        if not ctx:
            continue
        Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
        Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
        Quartz.CGContextScaleCTM(ctx, scale, scale)
        Quartz.CGContextDrawPDFPage(ctx, page)
        img = Quartz.CGBitmapContextCreateImage(ctx)
        if not img:
            continue
        dst = os.path.join(workdir, f"pdfp_{safe}_{i}.png")
        dest = Quartz.CGImageDestinationCreateWithURL(
            NSURL.fileURLWithPath_(dst), "public.png", 1, None)
        if dest:
            Quartz.CGImageDestinationAddImage(dest, img, None)
            if Quartz.CGImageDestinationFinalize(dest):
                imgs.append(dst)
    return imgs


def _render_pdf_pymupdf(path, workdir, safe, max_pages=4):
    """用 PyMuPDF(fitz) 渲染 PDF（pip 装，无外部依赖，Windows 友好）。"""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return []
    try:
        doc = fitz.open(path)
    except Exception:
        return []
    imgs = []
    for i in range(min(len(doc), max_pages)):
        page = doc[i]
        # 2x 缩放，足够 OCR
        mat = fitz.Matrix(2, 2)
        pix = page.get_pixmap(matrix=mat)
        dst = os.path.join(workdir, f"pdfp_{safe}_{i+1}.png")
        pix.save(dst)
        imgs.append(dst)
    doc.close()
    return imgs


def _render_pdf_pdftoppm(path, workdir, safe, max_pages=4):
    """用 poppler pdftoppm 渲染 PDF（兜底）。"""
    pdftoppm = _find_tool("pdftoppm")
    if not pdftoppm:
        return []
    prefix = os.path.join(workdir, "pdfp_" + safe)
    try:
        subprocess.run([pdftoppm, "-png", "-r", "150", "-l", str(max_pages), path, prefix],
                       capture_output=True, timeout=90)
    except Exception:
        return []
    imgs = []
    for fn in sorted(os.listdir(workdir)):
        if fn.startswith("pdfp_" + safe) and fn.endswith(".png"):
            imgs.append(os.path.join(workdir, fn))
    return imgs


def _extract_pdf(path, workdir):
    """PDF 解析：电子发票(有文字层)优先提文字；扫描件渲染多页再 OCR。

    Mac: Quartz 渲染 + pdftotext(若有) 文字层
    Windows/其他: PyMuPDF 渲染 + 文字层（无需 poppler），pdftoppm 兜底
    """
    safe = _safe_name(os.path.basename(path))
    out = []
    text_marker = None

    # 1. 文字层：优先 PyMuPDF（跨平台无依赖），否则 pdftotext
    text = _pdf_text_pymupdf(path)
    if not text:
        text = _pdf_text_pdftotext(path)
    if text and len(text) > 20:
        txt_dst = os.path.join(workdir, safe + ".txt")
        text_marker = txt_dst + ".pdftext"
        open(text_marker, "w", encoding="utf-8").write(text)

    # 2. 渲染页面为 png
    if IS_MAC:
        page_imgs = _render_pdf_quartz(path, workdir, safe)
        if not page_imgs:
            page_imgs = _render_pdf_pymupdf(path, workdir, safe)
    else:
        page_imgs = _render_pdf_pymupdf(path, workdir, safe)
        if not page_imgs:
            page_imgs = _render_pdf_pdftoppm(path, workdir, safe)

    if text_marker:
        out.append(text_marker)
    out.extend(page_imgs)
    return out


def _pdf_text_pymupdf(path):
    """用 PyMuPDF 提取 PDF 文字层。"""
    try:
        import fitz
        doc = fitz.open(path)
        text = "".join(doc[i].get_text() for i in range(len(doc)))
        doc.close()
        return text.strip()
    except Exception:
        return ""


def _pdf_text_pdftotext(path):
    """用 poppler pdftotext 提取文字层（兜底）。"""
    pdftotext = _find_tool("pdftotext")
    if not pdftotext:
        return ""
    try:
        r = subprocess.run([pdftotext, "-layout", path, "-"],
                           capture_output=True, timeout=30)
        return r.stdout.decode("utf-8", errors="ignore").strip()
    except Exception:
        return ""


def _safe_name(name):
    return re.sub(r"[^A-Za-z0-9_\-.]", "_", name)
