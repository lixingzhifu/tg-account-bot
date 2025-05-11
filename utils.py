# utils.py
import math
from datetime import datetime, timedelta

def ceil2(x: float) -> float:
    """保留两位小数并向上"""
    return math.ceil(x * 100) / 100

def now_ml() -> datetime:
    """当前马来西亚时间（UTC+8）"""
    return datetime.utcnow() + timedelta(hours=8)

def format_time(dt: datetime) -> str:
    """把 UTC dt 转为 +8 后格式化；NULL 时返回当前时间"""
    if not dt:
        dt = now_ml()
    return (dt + timedelta(hours=8)).strftime("%H:%M:%S")

def get_settings(chat_id: int, user_id: int):
    """从 settings 取当前配置，没配置时返回 rate=0"""
    from db import cursor
    cursor.execute("""
        SELECT currency, rate, fee_rate, commission_rate
        FROM settings
        WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    r = cursor.fetchone()
    if r:
        return r["currency"], r["rate"], r["fee_rate"], r["commission_rate"]
    return None, 0.0, 0.0, 0.0

def show_summary(chat_id: int, user_id: int) -> str:
    """拼接最新一笔和累计汇总的文本"""
    from db import cursor
    # 1. 最新一笔
    cursor.execute("""
        SELECT date, amount, rate, fee_rate, commission_rate, currency
        FROM transactions
        WHERE chat_id=%s AND user_id=%s
        ORDER BY id DESC
        LIMIT 1
    """, (chat_id, user_id))
    r = cursor.fetchone()
    t = format_time(r["date"])
    out_usdt = ceil2(r["amount"] * (1 - r["fee_rate"]/100) / r["rate"])
    comm_amt = ceil2(r["amount"] * (r["commission_rate"]/100))

    # 2. 累计笔数/金额
    cursor.execute("""
        SELECT COUNT(*) AS cnt, SUM(amount) AS total_rmb
        FROM transactions
        WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    s = cursor.fetchone()
    cnt   = s["cnt"] or 0
    total = ceil2(s["total_rmb"] or 0)

    # 3. 累计下发 USDT
    cursor.execute("""
        SELECT SUM(amount*(1 - fee_rate/100)/rate) AS send_sum
        FROM transactions
        WHERE chat_id=%s AND user_id=%s
    """, (chat_id, user_id))
    sp = cursor.fetchone()
    send_sum = ceil2(sp["send_sum"] or 0)

    return (
        f"{t} {r['amount']}×{1-r['fee_rate']/100:.2f}/{r['rate']} = {out_usdt} (USDT)\n"
        f"{t} {r['amount']}×{r['commission_rate']/100:.2f} = {comm_amt}【佣金】\n\n"
        f"已入款（{cnt}笔）：{total} (RMB)\n"
        f"总入款金额：{total} (RMB)\n"
        f"汇率：{r['rate']}\n"
        f"费率：{r['fee_rate']}%\n"
        f"佣金：{r['commission_rate']}%\n\n"
        f"应下发：{ceil2(total*(1-r['fee_rate']/100))}(RMB) | {send_sum}(USDT)\n"
        f"已下发：0.0(RMB) | 0.0(USDT)\n"
        f"未下发：{ceil2(total*(1-r['fee_rate']/100))}(RMB) | {send_sum}(USDT)\n\n"
        f"中介佣金应下发：{comm_amt}(RMB) | {ceil2(comm_amt/r['rate'])}(USDT)"
    )
