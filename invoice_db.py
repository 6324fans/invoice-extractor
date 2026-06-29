"""SQLite 存储层：发票记录 + 文件处理缓存。"""
import os
import sys
import sqlite3
from datetime import datetime


def _db_path():
    """打包后写到用户目录，避免只读临时目录。"""
    if getattr(sys, "frozen", False):
        base = os.path.join(os.path.expanduser("~"), ".invoice-extractor")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "invoices.db")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "invoices.db")


DB_PATH = _db_path()

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_type TEXT,
    invoice_code TEXT,
    invoice_no TEXT,
    invoice_date TEXT,
    invoice_date_iso TEXT,
    buyer TEXT,
    seller TEXT,
    amount TEXT,
    tax_amount TEXT,
    total_amount TEXT,
    check_code TEXT,
    machine_no TEXT,
    source_path TEXT,
    source_kind TEXT,
    wechat_time TEXT,
    ocr_text TEXT,
    thumb_path TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS file_cache (
    file_key TEXT PRIMARY KEY,
    processed INTEGER DEFAULT 0,
    is_invoice INTEGER DEFAULT 0,
    invoice_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_inv_wxtime ON invoices(wechat_time);
CREATE INDEX IF NOT EXISTS idx_inv_date ON invoices(invoice_date);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # 轻量迁移：旧库可能缺列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()}
    if "invoice_date_iso" not in cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN invoice_date_iso TEXT")
    if "tax_amount" not in cols:
        conn.execute("ALTER TABLE invoices ADD COLUMN tax_amount TEXT")
    conn.commit()
    return conn


def file_key(path, mtime):
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return f"{path}|{int(mtime.timestamp())}|{size}"


def cached_status(conn, key):
    row = conn.execute(
        "SELECT processed, is_invoice, invoice_id FROM file_cache WHERE file_key=?", (key,)
    ).fetchone()
    if row:
        return dict(row)
    return None


def mark_cache(conn, key, is_invoice, invoice_id=None):
    conn.execute(
        "INSERT OR REPLACE INTO file_cache(file_key, processed, is_invoice, invoice_id) "
        "VALUES(?,1,?,?)",
        (key, 1 if is_invoice else 0, invoice_id),
    )
    conn.commit()


def insert_invoice(conn, fields, source_path, source_kind, wechat_time, ocr_text, thumb_path):
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        """INSERT INTO invoices(
            invoice_type, invoice_code, invoice_no, invoice_date, invoice_date_iso, buyer, seller,
            amount, tax_amount, total_amount, check_code, machine_no, source_path, source_kind,
            wechat_time, ocr_text, thumb_path, created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            fields.get("invoice_type"), fields.get("invoice_code"),
            fields.get("invoice_no"), fields.get("invoice_date"),
            fields.get("invoice_date_iso"),
            fields.get("buyer"), fields.get("seller"),
            fields.get("amount"), fields.get("tax_amount"),
            fields.get("total_amount"),
            fields.get("check_code"), fields.get("machine_no"),
            source_path, source_kind,
            wechat_time.isoformat(timespec="seconds") if isinstance(wechat_time, datetime) else wechat_time,
            ocr_text, thumb_path, now,
        ),
    )
    conn.commit()
    return cur.lastrowid


def list_invoices(conn, start=None, end=None, q=None, date_field="wechat_time", limit=2000):
    """查询发票列表，支持按日期与关键字筛选。"""
    where = []
    params = []
    # invoice_date 存中文格式，按它筛选时改用 ISO 列
    if date_field == "invoice_date":
        date_field = "invoice_date_iso"
    if start:
        where.append(f"date({date_field}) >= ?")
        params.append(start)
    if end:
        where.append(f"date({date_field}) <= ?")
        params.append(end)
    if q:
        where.append("(seller LIKE ? OR buyer LIKE ? OR invoice_no LIKE ? OR invoice_code LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like, like]
    sql = "SELECT * FROM invoices"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {date_field} DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_invoice(conn, inv_id):
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    return dict(row) if row else None


def find_existing_by_no(conn, invoice_no):
    """按发票号码查重（发票号码全国唯一）。返回已存在的 id 或 None。"""
    if not invoice_no:
        return None
    row = conn.execute("SELECT id FROM invoices WHERE invoice_no=?", (invoice_no,)).fetchone()
    return row["id"] if row else None


def delete_invoice(conn, inv_id):
    """删除一条发票记录，返回被删记录(含 thumb_path/source_path)以便清理文件。"""
    row = conn.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    if not row:
        return None
    conn.execute("DELETE FROM invoices WHERE id=?", (inv_id,))
    conn.commit()
    return dict(row)


def count_all(conn):
    return conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0]


def reset_cache(conn):
    """清空缓存（重新扫描时用），可选清空发票表。"""
    conn.execute("DELETE FROM file_cache")
    conn.commit()
