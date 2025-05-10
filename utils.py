import re, math
from datetime import datetime, timedelta

def parse_trade_text(text):
    currency = None
    rate = fee = commission = None
    errors = []
    for line in text.replace("：",":").splitlines():
        line = line.strip().replace(" ", "")
        if line.startswith("设置货币:"):
            currency = re.sub(r"[^A-Za-z]", "", line.split(":",1)[1]).upper()
        elif line.startswith("设置汇率:"):
            try: rate = float(line.split(":",1)[1])
            except: errors.append("汇率格式错误")
        elif line.startswith("设置费率:"):
            try: fee = float(line.split(":",1)[1])
            except: errors.append("费率格式错误")
        elif line.startswith("中介佣金:"):
            try: commission = float(line.split(":",1)[1])
            except: errors.append("佣金格式错误")
    return currency, rate, fee, commission, errors

def human_now():
    dt = datetime.utcnow() + timedelta(hours=8)
    return dt.strftime("%H:%M:%S"), dt

def ceil2(x):
    return math.ceil(x*100)/100.0

def parse_amount_text(txt):
    txt = txt.strip()
    m = re.match(r"^(?:\+|加)\s*(\d+\.?\d*)$", txt)
    if m:
        return None, float(m.group(1))
    parts = re.findall(r"(.+?)(?:\+|加)\s*(\d+\.?\d*)", txt)
    if parts:
        return parts[0][0].strip(), float(parts[0][1])
    return None, None
