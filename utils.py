# utils.py

import math
import pytz
from datetime import datetime
from db import cursor

def ceil2(x):
    return math.ceil(x * 100) / 100.0

def format_time(dt):
    """
    如果传进来是 None，就返回当前马来西亚时间字符串；
    否则把 dt 当 UTC 时间，转成 Malaysia 时区再格式化。
    """
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    if not dt:
        return datetime.now(tz).strftime("%H:%M:%S")
    # 假设 dt 存的是 UTC
    dt = dt.replace(tzinfo=pytz.utc)
    return dt.astimezone(tz).strftime("%H:%M:%S")

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]
    return "RMB", 0.0, 0.0, 0.0

def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    records = cursor.fetchall()
    total = sum(r["amount"] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    converted = ceil2(total * (1 - fee/100) / rate) if rate else 0
    com_rmb  = ceil2(total * commission/100)
    com_usdt = ceil2(com_rmb / rate) if rate else 0

    lines = []
    for i, r in enumerate(records, 1):
        t = format_time(r.get("date"))
        after_fee = r["amount"] * (1 - r["fee_rate"]/100)
        usdt = ceil2(after_fee / r["rate"]) if r["rate"] else 0
        lines.append(f"{i}. {t} {r['amount']}*{(1 - r['fee_rate']/100):.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r["commission_rate"] > 0:
            com_amt = ceil2(r["amount"] * r["commission_rate"]/100)
            lines.append(f"{i}. {t} {r['amount']}*{r['commission_rate']/100:.2f} = {com_amt} 【佣金】")

    summary = "\n".join(lines)
    summary += (
        f"\n\n已入款（{len(records)}笔）：{total} ({currency})\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{ceil2(total*(1 - fee/100))}({currency}) | {converted} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1 - fee/100))}({currency}) | {converted} (USDT)\n"
    )
    if commission > 0:
        summary += f"\n中介佣金应下发：{com_rmb}({currency}) | {com_usdt} (USDT)"
    return summary
