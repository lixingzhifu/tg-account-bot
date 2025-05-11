# utils.py

import math
from datetime import datetime, timedelta
from db import cursor

def ceil2(x: float) -> float:
    return math.ceil(x * 100) / 100.0

def now_ml() -> datetime:
    """UTC+8 简易马来西亚时间"""
    return datetime.utcnow() + timedelta(hours=8)

def format_time(dt) -> str:
    """将 datetime 转成 HH:MM:SS；dt 可能为 None 时返回当前马来西亚时间"""
    if not dt:
        return now_ml().strftime("%H:%M:%S")
    # 假设数据库里存的是 UTC
    return (dt + timedelta(hours=8)).strftime("%H:%M:%S")

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
    after = ceil2(total * (1 - fee/100))
    usdt  = ceil2(after / rate) if rate else 0
    com_rmb  = ceil2(total * commission/100)
    com_usdt = ceil2(com_rmb / rate) if rate else 0

    lines = []
    for r in records:
        t = format_time(r["date"])
        after_fee = r["amount"] * (1 - r["fee_rate"]/100)
        us = ceil2(after_fee / r["rate"]) if r["rate"] else 0
        lines.append(f"{r['id']:03d}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {us}  {r['name']}")
        if r["commission_rate"]>0:
            cm = ceil2(r["amount"]*r["commission_rate"]/100)
            lines.append(f"{r['id']:03d}. {t} {r['amount']}*{r['commission_rate']/100:.3f} = {cm} 【佣金】")

    summary = "\n".join(lines) + "\n\n"
    summary += (
      f"已入款（{len(records)}笔）：{total} ({currency})\n"
      f"总入款金额：{total} ({currency})\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
      f"应下发：{after}({currency}) | {usdt}(USDT)\n"
      f"已下发：0.0({currency}) | 0.0(USDT)\n"
      f"未下发：{after}({currency}) | {usdt}(USDT)\n"
    )
    if commission>0:
        summary += f"\n中介佣金应下发：{com_rmb}({currency}) | {com_usdt}(USDT)"
    return summary
