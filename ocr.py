"""OCR 调用 + 发票字段提取。

跨平台：macOS 用 Vision 框架(Swift 编译的二进制)；Windows/其他用 Tesseract(pytesseract)。
"""
import os
import re
import sys
import subprocess
import shutil

IS_MAC = sys.platform == "darwin"
# 资源目录：打包后用 _MEIPASS，否则用源码目录
_RES_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
SWIFT_SRC = os.path.join(_RES_DIR, "wechat_ocr.swift")


def _bundled_bin_dir():
    """返回内包工具(tesseract/poppler)所在目录，打包后优先用程序旁的 bin/。

    查找顺序：环境变量 > 可执行文件旁 bin/ > _MEIPASS/bin > PATH
    """
    candidates = []
    if getattr(sys, "frozen", False):
        # 可执行文件旁的 bin/（PyInstaller onedir 模式）
        exe_dir = os.path.dirname(sys.executable)
        candidates.append(os.path.join(exe_dir, "bin"))
        candidates.append(os.path.join(_RES_DIR, "bin"))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


# OCR_BIN 是编译产物，需可写：打包后放用户目录
def _ocr_bin_path():
    if getattr(sys, "frozen", False):
        d = os.path.join(os.path.expanduser("~"), ".invoice-extractor")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, "wechat_ocr_bin")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "wechat_ocr_bin")
OCR_BIN = _ocr_bin_path()


def _find_tool(name):
    """查找外部工具：环境变量 > 内包 bin/ > PATH。Windows 自动加 .exe。"""
    env_key = {"tesseract": "TESSERACT_CMD",
               "pdftotext": "PDFTOTEXT_CMD",
               "pdftoppm": "PDFTOPPM_CMD"}.get(name)
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    bin_dir = _bundled_bin_dir()
    if bin_dir:
        cand = os.path.join(bin_dir, name + (".exe" if not IS_MAC else ""))
        if os.path.exists(cand):
            return cand
    found = shutil.which(name)
    return found


# Tesseract 可执行路径（Windows 内包或 PATH）
TESSERACT_CMD = _find_tool("tesseract")

# 命中其一即判定为发票
INVOICE_KEYWORDS = [
    "发票", "增值税", "价税合计", "发票号码", "发票代码",
    "机器编号", "开票日期", "纳税人识别", "货物或应税劳务",
    "INVOICE",
]
# 需要更强证据，避免"发票"二字误判（如广告里"开发票"）
STRONG_KEYWORDS = ["价税合计", "发票号码", "发票代码", "机器编号", "纳税人识别", "货物或应税劳务"]


def ensure_ocr_binary():
    """macOS：确保 Vision OCR 二进制存在，缺失则用 swiftc 编译。返回路径或 None。

    非 macOS 平台返回 None（走 Tesseract）。
    """
    if not IS_MAC:
        return None
    if os.path.exists(OCR_BIN) and os.access(OCR_BIN, os.X_OK):
        return OCR_BIN
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    try:
        subprocess.run(
            [swiftc, SWIFT_SRC, "-o", OCR_BIN],
            check=True, capture_output=True, timeout=120,
        )
        os.chmod(OCR_BIN, 0o755)
        return OCR_BIN if os.path.exists(OCR_BIN) else None
    except Exception:
        return None


def ocr_available():
    """当前平台 OCR 是否可用。"""
    if IS_MAC:
        return ensure_ocr_binary() is not None
    return TESSERACT_CMD is not None


def _setup_tessdata():
    """设置 TESSDATA_PREFIX 指向内包的 tessdata 目录（含中文语言包）。"""
    if os.environ.get("TESSDATA_PREFIX"):
        return
    bin_dir = _bundled_bin_dir()
    if not bin_dir:
        return
    # tessdata 在 bin 旁或 bin 内
    for cand in (os.path.join(os.path.dirname(bin_dir), "tessdata"),
                 os.path.join(bin_dir, "tessdata")):
        if os.path.isdir(cand):
            os.environ["TESSDATA_PREFIX"] = os.path.dirname(cand)
            return


def _ocr_with_tesseract(paths, timeout=60):
    """Windows/其他：用 Tesseract 识别，返回 {path: text}。"""
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return {p: "__OCR_UNAVAILABLE__" for p in paths}
    if TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD
    # 设置 tessdata 目录（内包时指向 bin 旁的 tessdata）
    _setup_tessdata()
    results = {}
    for p in paths:
        try:
            img = Image.open(p)
            text = pytesseract.image_to_string(
                img, lang="chi_sim+chi_tra+eng", config="--psm 6"
            )
            results[p] = text
        except Exception:
            results[p] = "__OCR_FAIL__"
    return results


def ocr_images(paths, timeout=60):
    """对一组图片路径调用 OCR，返回 {path: text}。按平台自动选引擎。"""
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        return {}

    if not IS_MAC:
        # Windows / Linux：Tesseract
        if not TESSERACT_CMD:
            return {p: "__OCR_UNAVAILABLE__" for p in paths}
        return _ocr_with_tesseract(paths, timeout)

    # macOS：Vision 二进制
    bin_path = ensure_ocr_binary()
    if not bin_path:
        return {p: "__OCR_UNAVAILABLE__" for p in paths}
    try:
        proc = subprocess.run(
            [bin_path] + paths,
            capture_output=True, text=True, timeout=timeout * max(1, len(paths)),
        )
    except subprocess.TimeoutExpired:
        return {p: "__OCR_TIMEOUT__" for p in paths}
    out = proc.stdout
    results = {}
    cur_path = None
    cur_text = []
    mode = None
    for line in out.splitlines():
        if line == "<<<FILE>>>":
            mode = "file"
        elif line == "<<<TEXT>>>":
            mode = "text"
            cur_text = []
        elif line == "<<<END>>>":
            text = "\n".join(cur_text)
            if text == "EMPTY":
                text = ""
            if cur_path:
                results[cur_path] = text
            cur_path = None
            cur_text = []
            mode = None
        else:
            if mode == "file":
                cur_path = line.strip()
            elif mode == "text":
                cur_text.append(line)
    # 兜底：未解析到的标记为错误
    for p in paths:
        results.setdefault(p, "__OCR_FAIL__")
    return results


def is_invoice(text):
    if not text:
        return False
    has_any = any(k in text for k in INVOICE_KEYWORDS)
    has_strong = any(k in text for k in STRONG_KEYWORDS)
    return has_any and has_strong


def _search(pattern, text, flags=0):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


COMPANY_TAIL = (
    r"(?:有限公司|股份有限公司|有限责任公司|集团(?:有限公司)?|中心|厂|店|"
    r"个体工商户|医院|学校|研究院|事务所|工作室|分公司|合伙企业|药房|药店)"
)
# 公司名（含括号、汉字，2~40 字，以公司类后缀结尾）
COMPANY_RE = re.compile(rf"([一-龥（）()A-Za-z0-9]{{2,40}}{COMPANY_TAIL})")
PROVINCE_RE = re.compile(
    r"(北京|天津|上海|重庆|河北|河南|云南|辽宁|黑龙江|湖南|安徽|山东|新疆|江苏|"
    r"浙江|江西|湖北|广西|甘肃|山西|内蒙古|陕西|吉林|福建|贵州|广东|四川|青海|西藏|海南|宁夏)"
)


def _date_to_iso(s):
    """'2024年03月03日' -> '2024-03-03'。"""
    if not s:
        return None
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _norm(text):
    """去除所有空白，便于结构匹配。"""
    return re.sub(r"\s+", "", text)


def _norm_digits(s):
    if not s:
        return s
    return (s.replace("O", "0").replace("o", "0")
             .replace("I", "1").replace("l", "1")
             .replace("S", "5").replace("B", "8"))


def _detect_type(norm):
    """用关键字判定发票类型，尽量拼出'XX增值税普通发票'。"""
    prov = ""
    m = PROVINCE_RE.search(norm[:30])
    if m:
        prov = m.group(1)
    # 注意"专用"可能来自"发票专用章"，故普通优先；专用发票不会含"普通"
    if "普通" in norm:
        return f"{prov}增值税普通发票" if prov else "增值税普通发票"
    if "专用发票" in norm:
        return f"{prov}增值税专用发票" if prov else "增值税专用发票"
    return None


def extract_fields(text):
    """从 OCR 文本中提取发票字段。返回 dict，可能部分为 None。"""
    if not text:
        return None
    f = {}
    norm = _norm(text)

    f["invoice_type"] = _detect_type(norm)

    # 发票代码 / 号码（OCR 可能误识"发票"为"发樑/发柔/发樂"等，做容错）
    f["invoice_code"] = _norm_digits(
        _search(r"发[票樑柔樂]代码[:：]?([0-9OoIlSsB]{8,})", norm)
    )
    f["invoice_no"] = _norm_digits(
        _search(r"发[票樑柔樂]号[码碼]?[:：]?([0-9OoIlSsB]{6,})", norm)
    )

    # 开票日期
    f["invoice_date"] = _search(
        r"开[票栗]?日期[:：]?([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)", norm
    )
    f["invoice_date_iso"] = _date_to_iso(f["invoice_date"])

    # 校验码
    cm = _search(r"校[验税]码[:：]?([0-9 ]{10,})", text)
    f["check_code"] = re.sub(r"\s+", " ", cm).strip() if cm else None

    # 机器编号
    f["machine_no"] = _norm_digits(
        _search(r"机器编号[:：]?([0-9OoIlSsB]{8,})", norm)
    )

    # 价税合计（小写金额）：优先 (小写)¥xx，再 价税合计 后 ¥/￥，再 (写)¥xx
    amt = _search(r"[（(]小写[^0-9]{0,4}[¥￥]([0-9]+\.[0-9]{1,2})", norm)
    if not amt:
        amt = _search(r"价税合计.{0,40}?[¥￥]([0-9]+\.[0-9]{1,2})", norm)
    if not amt:
        amt = _search(r"[（(]写[)）][：:]?[¥￥]([0-9]+\.[0-9]{1,2})", norm)
    f["total_amount"] = amt

    # 合计金额
    f["amount"] = _search(r"合计[:：]?[¥￥]?([0-9]+\.[0-9]{1,2})", norm)

    # 购买方 / 销售方名称：按"价税合计"分块，前块取购买方，后块取销售方
    f["buyer"], f["seller"] = _extract_parties(text, norm)
    return f


def _extract_parties(text, norm):
    """购买方在'价税合计'之前，销售方在其之后。各自取首个公司名/人名。"""
    idx = text.find("价税合计")
    first_half = text if idx < 0 else text[:idx]
    second_half = "" if idx < 0 else text[idx:]

    buyer = _first_company(first_half) or _first_person(first_half)
    seller = _first_company(second_half)
    return buyer, seller


def _first_company(text):
    m = COMPANY_RE.search(text)
    return m.group(1).strip() if m else None


def _first_person(text):
    """购买方为个人时，取'名称：'后 2~5 字汉字。"""
    m = re.search(r"名\s*称\s*[：:]\s*([一-龥]{2,5})(?![一-龥])", text)
    if m:
        return m.group(1)
    # 容忍"名称"被拆成"名X："
    m = re.search(r"名[一-龥]?\s*[：:]\s*([一-龥]{2,5})(?![一-龥])", text)
    return m.group(1) if m else None
