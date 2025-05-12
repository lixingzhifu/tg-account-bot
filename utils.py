# utils.py

import re
import math
from datetime import datetime

def parse_trade_text(text: str):
    """
    解析“设置交易指令”那段多行文本，
    返回 (currency, rate, fee_rate, commission_rate, errors_list)
    """
    currency = None
    rate = None
    fee = None
    com = None
    errors = []

    # 按行拆
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("设置货币："):
            currency = line.replace("设置货币：", "").strip()
        elif line.startswith("设置汇率："):
            try:
                rate = float(line.replace("设置汇率：", "").strip())
            except:
                errors.append(f"汇率不是数字：{line}")
        elif line.startswith("设置费率："):
            try:
                fee = float(line.replace("设置费率：", "").strip())
            except:
                errors.append(f"费率不是数字：{line}")
        elif line.startswith("中介佣金："):
            try:
                com = float(line.replace("中介佣金：", "").strip())
            except:
                errors.append(f"佣金不是数字：{line}")

    # 至少需要汇率
    if rate is None:
        errors.append("缺少“设置汇率：数字”")

    return currency, rate, fee, com, errors


def parse_amount_text(text: str):
    """
    从“+1000” / “入1000” / “入笔1000”字样中提取金额
    返回 (None, amount) 或 (None, None) 代表不匹配
    """
    m = re.search(r"([+]?|入笔|入)\s*(\d+(\.\d+)?)", text)
    if not m:
        return None, None
    amt = float(m.group(2))
    return None, amt


def human_now():
    """
    返回 （本地时间字符串, UTC datetime 对象）
    local_str 用于直接展示，dt 用于存库
    """
    # 马来西亚是 UTC+8
    dt_utc = datetime.utcnow()
    local = (dt_utc.hour + 8) % 24
    hms = f"{local:02d}:{dt_utc.minute:02d}:{dt_utc.second:02d}"
    return hms, dt_utc


def ceil2(x: float) -> float:
    """
    向上保留两位小数
    """
    return math.ceil(x * 100) / 100.0
