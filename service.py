"""扫描编排：扫描器 + 解包 + OCR + 入库。"""
import os
import sys
import shutil
import subprocess
import tempfile
from datetime import datetime

import extractors
import ocr as ocrmod
import invoice_db


def _writable_base():
    """返回可写的基础目录。

    打包后( PyInstaller onefile )源码目录是只读临时解压目录，
    数据库/图片等可写文件应放在用户目录下。
    """
    if getattr(sys, "frozen", False):
        # 打包模式：用用户目录
        base = os.path.join(os.path.expanduser("~"), ".invoice-extractor")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(base, exist_ok=True)
    return base


BASE_DIR = _writable_base()
DATA_DIR = os.path.join(BASE_DIR, "data")
IMG_DIR = os.path.join(DATA_DIR, "images")
THUMB_DIR = os.path.join(DATA_DIR, "thumbs")
for _d in (IMG_DIR, THUMB_DIR):
    os.makedirs(_d, exist_ok=True)


def get_search_base(config):
    """返回搜索的根路径：配置 wx_path 优先，否则用默认微信根目录。

    支持：
      - 任意大路径（微信根目录、用户目录、或任意父目录），都会递归搜索其下所有子目录
      - 留空：用默认微信根目录
    不再要求路径是微信结构，纯按扩展名递归扫描。
    """
    custom = (config.get("wx_path") or "").strip()
    if custom and os.path.isdir(custom):
        return custom
    return extractors.DEFAULT_WX_ROOT


def _make_thumb(src_img, inv_id):
    """生成缩略图，返回相对路径。"""
    thumb = os.path.join(THUMB_DIR, f"{inv_id}.jpg")
    try:
        subprocess.run(
            ["sips", "-Z", "240", "-s", "format", "jpeg", src_img, "--out", thumb],
            capture_output=True, timeout=20,
        )
        if not os.path.exists(thumb):
            shutil.copy(src_img, thumb)
    except Exception:
        try:
            shutil.copy(src_img, thumb)
        except Exception:
            return None
    return thumb


def _save_full_image(src_img, inv_id):
    """持久化原图副本，返回路径。"""
    ext = os.path.splitext(src_img)[1].lower() or ".png"
    dst = os.path.join(IMG_DIR, f"{inv_id}{ext}")
    try:
        shutil.copy(src_img, dst)
        return dst
    except Exception:
        return None


def scan(start, end, config, progress_cb=None):
    """扫描 [start, end] 日期范围，提取发票入库。

    start/end: datetime
    progress_cb(done, total, msg)
    返回 dict: {scanned, invoices, skipped, errors}
    """
    base = get_search_base(config)
    if not base or not os.path.isdir(base):
        return {"error": "搜索路径不存在，请在设置中配置正确路径"}

    candidates = extractors.iter_candidate_files(base, start, end)
    total = len(candidates)
    conn = invoice_db.get_conn()
    result = {"scanned": 0, "invoices": 0, "skipped": 0, "errors": 0, "new_invoices": []}

    for i, (path, kind, mtime) in enumerate(candidates):
        if progress_cb:
            progress_cb(i, total, os.path.basename(path))
        key = invoice_db.file_key(path, mtime)
        cached = invoice_db.cached_status(conn, key)
        if cached and cached.get("processed"):
            result["skipped"] += 1
            continue

        result["scanned"] += 1
        workdir = tempfile.mkdtemp(prefix="wxscan_", dir=BASE_DIR)
        try:
            img_paths = extractors.extract_images(path, workdir)
            # 收集 (文本, 展示图) 对：pdftext 标记自带文本(无图)；真图片需 OCR(自带图)
            items = []  # [(text_or_None, display_img_or_None)]
            for ip in img_paths:
                if ip.endswith(".pdftext"):
                    try:
                        t = open(ip, "r", errors="ignore").read()
                        items.append((t if t.strip() else None, None))
                    except Exception:
                        items.append((None, None))
                else:
                    items.append((None, ip))

            # 批量 OCR 待识别图片
            to_ocr = [img for (t, img) in items if t is None and img]
            ocr_results = ocrmod.ocr_images(to_ocr) if to_ocr else {}
            for idx, (t, img) in enumerate(items):
                if t is None and img:
                    items[idx] = (ocr_results.get(img, ""), img)

            # 任一可用的展示图（PDF 文字层无图时，用渲染图展示）
            fallback_img = next((img for (t, img) in items if img), None)

            file_is_invoice = False
            for text, img in items:
                if not text or text.startswith("__OCR"):
                    continue
                if ocrmod.is_invoice(text):
                    fields = ocrmod.extract_fields(text)
                    # 按发票号码去重（同张发票可能来自 zip + 缓存 + RWTemp 多来源）
                    if invoice_db.find_existing_by_no(conn, fields.get("invoice_no")):
                        file_is_invoice = True
                        continue
                    display = img or fallback_img
                    inv_id = invoice_db.insert_invoice(
                        conn, fields, path, kind, mtime, text, None
                    )
                    if display:
                        _save_full_image(display, inv_id)
                        thumb = _make_thumb(display, inv_id)
                        if thumb:
                            conn.execute("UPDATE invoices SET thumb_path=? WHERE id=?", (thumb, inv_id))
                            conn.commit()
                    file_is_invoice = True
                    result["invoices"] += 1
                    result["new_invoices"].append({
                        "id": inv_id,
                        "seller": fields.get("seller"),
                        "invoice_no": fields.get("invoice_no"),
                        "total_amount": fields.get("total_amount"),
                    })
            invoice_db.mark_cache(conn, key, file_is_invoice)
        except Exception as e:
            result["errors"] += 1
            invoice_db.mark_cache(conn, key, False)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    if progress_cb:
        progress_cb(total, total, "完成")
    conn.close()
    return result
