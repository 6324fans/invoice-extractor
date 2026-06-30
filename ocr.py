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
    if not TESSERACT_CMD:
        return False
    # 内包模式：tesseract.exe 存在还不够，必须能加载中文语言包才算就绪，
    # 否则会出现"OCR 就绪"却每张图都识别失败的假象。
    td = os.environ.get("TESSDATA_PREFIX") or _tessdata_dir()
    if td:
        return os.path.exists(os.path.join(td, "chi_sim.traineddata"))
    # 系统 tesseract（开发模式未内包）：信任其自带 tessdata
    return True


def _tessdata_dir():
    """返回内包 tessdata 目录（含 *.traineddata 的目录本身），找不到返回 None。"""
    bin_dir = _bundled_bin_dir()
    if not bin_dir:
        return None
    # tessdata 在 bin 旁或 bin 内
    for cand in (os.path.join(os.path.dirname(bin_dir), "tessdata"),
                 os.path.join(bin_dir, "tessdata")):
        if os.path.isdir(cand):
            return cand
    return None


def _setup_tessdata():
    """设置 TESSDATA_PREFIX 指向内包的 tessdata 目录（含中文语言包）。

    必须指向 tessdata 目录本身（即含 *.traineddata 的目录），而非其父目录——
    否则 Windows 版 tesseract 找不到语言包，OCR 会全部静默失败（返回空文本）。
    """
    if os.environ.get("TESSDATA_PREFIX"):
        return
    td = _tessdata_dir()
    if td:
        os.environ["TESSDATA_PREFIX"] = td


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
    """从 OCR 文本中提取发票字段。返回 dict，可能部分为 None。

    对 OCR 常见的标签模糊/格式漂移做容错：日期、校验码、金额、购销方在
    标签匹配失败时启用全文兜底或版面结构启发式，尽量把已识别出的信息取出来。
    """
    if not text:
        return None
    f = {}
    norm = _norm(text)

    f["invoice_type"] = _detect_type(norm)

    # 发票代码 / 号码（OCR 可能误识"发票"为"发樑/发柔/发樂"等，做容错）
    f["invoice_code"] = _norm_digits(
        _search(r"发[票樑柔樂]?代[码碼]?[:：]?([0-9OoIlSsB]{8,})", norm)
    )
    f["invoice_no"] = _norm_digits(
        _search(r"发[票樑柔樂]?号[码碼]?[:：]?([0-9OoIlSsB]{6,})", norm)
    )

    # 开票日期：标签+年月日 → 全文任意年月日 → 标签在但月/日被识丢
    d = _search(r"开[票栗]?日期[:：]?([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)", norm)
    if not d:
        d = _search(r"([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)", norm)
    if not d:
        m = re.search(r"开[票栗]?日期[:：]?\s*(\d{4})\D{0,2}(\d{1,2})\D{0,3}(\d{1,2})", text)
        if m:
            d = f"{m.group(1)}年{int(m.group(2))}月{int(m.group(3))}日"
    f["invoice_date"] = d
    f["invoice_date_iso"] = _date_to_iso(d)

    # 校验码：要求多组数字由空格分隔（常4组5位），避免误匹配"代码:""号码:"
    cm = _search(r"[码碼][:：]\s*((?:[0-9]+ +){1,}[0-9]+)", text)
    f["check_code"] = re.sub(r"\s+", " ", cm).strip() if cm else None

    # 机器编号
    f["machine_no"] = _norm_digits(
        _search(r"机器编号[:：]?([0-9OoIlSsB]{8,})", norm)
    )

    # 金额：收集所有 ¥xx.xx，按"一个≈另两个之和"识别价税合计/金额/税额
    vals = [float(m.group(1)) for m in re.finditer(r"[¥￥]([0-9]+\.[0-9]{1,2})", norm)]
    f["amount"], f["tax_amount"], f["total_amount"] = _extract_amounts(vals)

    # 购买方 / 销售方
    f["buyer"], f["seller"] = _extract_parties(text)
    return f


def _extract_amounts(vals):
    """vals: 所有 ¥ 金额（浮点，按出现顺序）。返回 (金额合计, 税额, 价税合计)。

    规则：若存在一个值≈另两个之和 → 那个是价税合计，另两个按大小分金额/税额；
    只有 2 个 → 大者=金额合计，小者=税额，价税=二者和；
    1 个 → 视为价税合计；3+ 但无和关系 → 取最后两个为金额/税额（合计行常在末尾）。
    """
    if not vals:
        return None, None, None
    if len(vals) >= 3:
        for i in range(len(vals)):
            for j in range(len(vals)):
                if i == j:
                    continue
                s = round(vals[i] + vals[j], 2)
                for k in range(len(vals)):
                    if k != i and k != j and abs(vals[k] - s) < 0.01:
                        amt, tax = (vals[i], vals[j]) if vals[i] >= vals[j] else (vals[j], vals[i])
                        return f"{amt:.2f}", f"{tax:.2f}", f"{vals[k]:.2f}"
        # 无 a+b=c 关系：合计行通常在末尾，取最后两个
        a, b = vals[-2], vals[-1]
        amt, tax = (a, b) if a >= b else (b, a)
        return f"{amt:.2f}", f"{tax:.2f}", f"{amt + tax:.2f}"
    if len(vals) == 2:
        amt, tax = (vals[0], vals[1]) if vals[0] >= vals[1] else (vals[1], vals[0])
        return f"{amt:.2f}", f"{tax:.2f}", f"{amt + tax:.2f}"
    return None, None, f"{vals[0]:.2f}"


def _extract_parties(text):
    """购买方 / 销售方。

    销售方：取"合计行"与"收款人行"之间的首个公司名（发票右下角销售方栏）；
    购买方：货物表头之前的首个公司名，否则取"名称:"后的个人名。
    即便"价税合计"等分隔关键字被 OCR 识错，也能按版面结构定位。
    """
    lines = text.splitlines()
    he_idx = -1
    for i, ln in enumerate(lines):
        if "合" in ln and ("¥" in ln or "￥" in ln):
            he_idx = i
            break
    if he_idx < 0:
        for i, ln in enumerate(lines):
            if "¥" in ln or "￥" in ln:
                he_idx = i
                break
    shou_idx = -1
    for i, ln in enumerate(lines):
        if "收款人" in ln:
            shou_idx = i
            break
    lo = he_idx + 1 if he_idx >= 0 else len(lines) // 2
    hi = shou_idx if shou_idx > lo else len(lines)
    seller = None
    for ln in lines[lo:hi]:
        m = COMPANY_RE.search(ln)
        if m:
            seller = m.group(1).strip()
            break
    if not seller:
        m = re.search(r"[销銷]售方", text)
        if m:
            hits = list(COMPANY_RE.finditer(text[:m.start()]))
            if hits:
                seller = hits[-1].group(1).strip()

    # 购买方：货物表头之前的首个公司名，否则个人名
    head_idx = len(text)
    for kw in ("货物或应税", "规格型号", "规格型號", "單位", "数量", "金 額", "金额"):
        idx = text.find(kw)
        if 0 <= idx < head_idx:
            head_idx = idx
    head = text[:head_idx] if head_idx < len(text) else text
    buyer = None
    m = COMPANY_RE.search(head)
    if m:
        buyer = m.group(1).strip()
    if not buyer:
        buyer = _first_person(head)
    return buyer, seller


def _first_company(text):
    m = COMPANY_RE.search(text)
    return m.group(1).strip() if m else None


def _first_person(text):
    """购买方为个人时，取'名称：'后 2~5 字汉字；容忍'名称'被识成'名    Hp:'等。"""
    m = re.search(r"名\s*称\s*[：:]\s*([一-龥]{2,5})(?![一-龥])", text)
    if m:
        return m.group(1)
    # 容忍"名"与冒号之间混入少量其它字符（OCR 把"名称"识成"名 Hp"等）
    m = re.search(r"名[^\n:：]{0,8}[:：]\s*([一-龥]{2,5})(?![一-龥])", text)
    return m.group(1) if m else None
