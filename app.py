"""微信发票提取 Web 服务。运行: python3 app.py"""
import json
import os
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, send_file, render_template, abort

import extractors
import invoice_db
import ocr as ocrmod
import service

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"),
            static_folder=os.path.join(BASE_DIR, "static"))


def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            return json.load(open(CONFIG_PATH, "r", encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_config(cfg):
    json.dump(cfg, open(CONFIG_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "GET":
        cfg = load_config()
        default_root = extractors.DEFAULT_WX_ROOT
        custom = (cfg.get("wx_path") or "").strip()
        # 实际搜索路径 = 配置路径 or 默认根目录
        search_base = custom if (custom and os.path.isdir(custom)) else default_root
        return jsonify({
            "wx_path": custom,
            "default_root": default_root,
            "search_base": search_base,
            "exists": os.path.isdir(search_base),
        })
    data = request.get_json(force=True)
    cfg = load_config()
    cfg["wx_path"] = (data.get("wx_path") or "").strip()
    save_config(cfg)
    return jsonify({"ok": True, "wx_path": cfg["wx_path"]})


@app.route("/api/status")
def api_status():
    ocr_ready = ocrmod.ocr_available()  # 跨平台：mac 看 Vision 二进制，Win 看 Tesseract
    cfg = load_config()
    custom = (cfg.get("wx_path") or "").strip()
    search_base = custom if (custom and os.path.isdir(custom)) else extractors.DEFAULT_WX_ROOT
    return jsonify({"ocr_ready": ocr_ready, "search_base": search_base,
                    "exists": os.path.isdir(search_base)})


@app.route("/api/scan", methods=["POST"])
def api_scan():
    data = request.get_json(force=True) or {}
    start_str = data.get("start")
    end_str = data.get("end")
    force = data.get("force", False)
    if not start_str:
        return jsonify({"error": "缺少 start 日期"}), 400
    start = datetime.strptime(start_str, "%Y-%m-%d")
    end = datetime.strptime(end_str, "%Y-%m-%d") if end_str else start
    if force:
        conn = invoice_db.get_conn()
        invoice_db.reset_cache(conn)
        conn.close()
    cfg = load_config()
    result = service.scan(start, end, cfg)
    return jsonify(result)


@app.route("/api/invoices")
def api_invoices():
    start = request.args.get("start")
    end = request.args.get("end")
    q = request.args.get("q")
    date_field = request.args.get("date_field", "wechat_time")
    if date_field not in ("wechat_time", "invoice_date"):
        date_field = "wechat_time"
    conn = invoice_db.get_conn()
    rows = invoice_db.list_invoices(conn, start=start, end=end, q=q, date_field=date_field)
    total = invoice_db.count_all(conn)
    conn.close()
    return jsonify({"total": total, "count": len(rows), "items": rows})


@app.route("/api/invoice/<int:inv_id>")
def api_invoice_detail(inv_id):
    conn = invoice_db.get_conn()
    inv = invoice_db.get_invoice(conn, inv_id)
    conn.close()
    if not inv:
        abort(404)
    return jsonify(inv)


@app.route("/api/thumb/<int:inv_id>")
def api_thumb(inv_id):
    conn = invoice_db.get_conn()
    inv = invoice_db.get_invoice(conn, inv_id)
    conn.close()
    if not inv or not inv.get("thumb_path") or not os.path.exists(inv["thumb_path"]):
        abort(404)
    return send_file(inv["thumb_path"])


@app.route("/api/image/<int:inv_id>")
def api_image(inv_id):
    conn = invoice_db.get_conn()
    inv = invoice_db.get_invoice(conn, inv_id)
    conn.close()
    if not inv:
        abort(404)
    # 优先持久化原图，否则缩略图，否则来源文件
    for candidate in []:
        pass
    img_dir = service.IMG_DIR
    # 找 data/images/<id>.*
    for fn in os.listdir(img_dir):
        if fn.startswith(f"{inv_id}."):
            return send_file(os.path.join(img_dir, fn))
    if inv.get("thumb_path") and os.path.exists(inv["thumb_path"]):
        return send_file(inv["thumb_path"])
    abort(404)


@app.route("/api/invoice/<int:inv_id>", methods=["DELETE"])
def api_delete_invoice(inv_id):
    conn = invoice_db.get_conn()
    inv = invoice_db.delete_invoice(conn, inv_id)
    conn.close()
    if not inv:
        abort(404)
    # 清理持久化的原图与缩略图
    for fn in list(os.listdir(service.IMG_DIR)):
        if fn.startswith(f"{inv_id}."):
            try:
                os.remove(os.path.join(service.IMG_DIR, fn))
            except Exception:
                pass
    if inv.get("thumb_path") and os.path.exists(inv["thumb_path"]):
        try:
            os.remove(inv["thumb_path"])
        except Exception:
            pass
    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """清空缓存与所有发票记录，重新开始。"""
    conn = invoice_db.get_conn()
    conn.execute("DELETE FROM invoices")
    conn.execute("DELETE FROM file_cache")
    conn.commit()
    conn.close()
    # 清理持久化图片
    for d in (service.IMG_DIR, service.THUMB_DIR):
        for fn in os.listdir(d):
            try:
                os.remove(os.path.join(d, fn))
            except Exception:
                pass
    return jsonify({"ok": True})


if __name__ == "__main__":
    # 预编译 OCR 二进制
    ocrmod.ensure_ocr_binary()
    port = int(os.environ.get("PORT", "5000"))
    print(f"启动: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
