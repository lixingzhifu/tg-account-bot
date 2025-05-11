# utils.py

import math
from datetime import datetime
from psycopg2.extras import RealDictCursor
import psycopg2

# --- 把你的 DB 连接放到 db.py，然后在这里从 db.py import ---
from db import conn, cursor

def ceil2(n):
    """向上保留两位小数"""
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id, user_id):
    """从 settings 表里取当前 chat_id+user_id 的配置"""
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate '
        'FROM settings WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']
    # 如果没找着，就返回默认值
    return 'RMB', 0, 0, 0

def show_summary(chat_id, user_id):
    """汇总函数示例，transactions.py 会用到它来生成文字报告"""
    cursor.execute(
        'SELECT amount, fee_rate, rate, commission_rate, date, name '
        'FROM transactions WHERE chat_id=%s AND user_id=%s',
        (chat_id, user_id)
    )
    records = cursor.fetchall()
    total = sum(r['amount'] for r in records)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    # …你的汇总逻辑…
    # 返回一大段字符串
    return "（这里填你的 show_summary 实现）"
