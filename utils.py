# utils.py
import math
import pytz
from datetime import datetime

def ceil2(x):
    return math.ceil(x*100)/100.0

def format_time(dt: datetime) -> str:
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    return dt.astimezone(tz).strftime("%H:%M:%S")

def get_settings(chat_id, user_id):
    from db import cursor
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings "
        "WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]
    return "RMB", 0, 0, 0

def show_summary(chat_id, user_id):
    # 直接把之前写好的 show_summary 逻辑粘过来
    ...
