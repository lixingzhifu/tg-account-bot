import os
import re
import math
from datetime import datetime, timedelta
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor

TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(TOKEN)

conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 建表
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    user_id BIGINT,
    name TEXT,
    amount DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    fee_rate DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date TIMESTAMP,
    message_id BIGINT
);
""")
conn.commit()

def now_malaysia():
    # 服务器如果是 UTC，只要加 8 小时即可；如果已经是 GMT+8 则可以直接 datetime.now()
    return datetime.now() + timedelta(hours=8)

def fmt_time(dt):
    return dt.strftime('%H:%M:%S')

def ceil2(x):
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else None

def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    rows = cursor.fetchall()
    total = sum(r['amount'] for r in rows)
    cur, rate, fee, comm = get_settings(chat_id, user_id)
    after_fee = ceil2(total * (1 - fee / 100))
    after_fee_usdt = ceil2(after_fee / rate) if rate else 0
    comm_rmb = ceil2(total * comm / 100)
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(rows, 1):
        t = fmt_time(r['date'])
        usdt = ceil2(r['amount'] * (1 - r['fee_rate']/100) / r['rate']) if r['rate'] else 0
        lines.append(f"{idx}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r['commission_rate'] > 0:
            lines.append(f"{idx}. {t} {r['amount']}*{r['commission_rate']/100:.3f} = {ceil2(r['amount']*r['commission_rate']/100)} 【佣金】")
    body = "\n".join(lines)

    summary = (
        f"\n\n已入款（{len(rows)}笔）：{total} ({cur})\n"
        f"总入款金额：{total} ({cur})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{comm}%\n\n"
        f"应下发：{after_fee}({cur}) | {after_fee_usdt} (USDT)\n"
        f"已下发：0.0({cur}) | 0.0 (USDT)\n"
        f"未下发：{after_fee}({cur}) | {after_fee_usdt} (USDT)\n"
    )
    if comm > 0:
        summary += f"\n中介佣金应下发：{comm_rmb}({cur}) | {comm_usdt} (USDT)"
    return body + summary

@bot.message_handler(commands=['start'])
def cmd_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📊 汇总')
    kb.row('❌ 删除订单', '🛠️ 帮助')
    bot.send_message(m.chat.id, "欢迎使用 LX 记账机器人 ✅", reply_markup=kb)

@bot.message_handler(func=lambda m: '设置交易' in (m.text or ''))
def cmd_trade(m):
    bot.reply_to(m,
        "请按以下格式发送：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：9\n"
        "设置费率：2\n"
        "中介佣金：0.5"
    )

@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def set_trade(m):
    chat, user = m.chat.id, m.from_user.id
    lines = m.text.replace('：',':').splitlines()
    c = r = f = cm = None
    for L in lines:
        if L.startswith('设置货币'): c = L.split(':',1)[1].strip()
        if L.startswith('设置汇率'): r = float(re.findall(r'\d+\.?\d*',L)[0])
        if L.startswith('设置费率'): f = float(re.findall(r'\d+\.?\d*',L)[0])
        if L.startswith('中介佣金'): cm= float(re.findall(r'\d+\.?\d*',L)[0])
    if r is None:
        return bot.reply_to(m, "❌ 请先设置汇率：设置汇率：9")
    cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE
          SET currency=EXCLUDED.currency,
              rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate,
              commission_rate=EXCLUDED.commission_rate
    """, (chat, user, c or 'RMB', r, f or 0, cm or 0))
    conn.commit()
    bot.reply_to(m,
        f"✅ 设置成功\n货币：{c or 'RMB'}\n汇率：{r}\n费率：{f or 0}%\n佣金：{cm or 0}%"
    )

@bot.message_handler(func=lambda m: re.match(r'^[+\-]\s*\d+(\.\d+)?$', m.text or ''))
def handle_amount(m):
    chat, user = m.chat.id, m.from_user.id
    st = get_settings(chat, user)
    if not st:
        return bot.reply_to(m, "❌ 请先 /start → 设置交易")
    cur, rate, fee, comm = st
    sign = 1 if m.text.strip().startswith('+') else -1
    amt = float(re.findall(r'\d+\.?\d*', m.text)[0]) * sign
    dt = now_malaysia()
    if sign > 0:
        name = m.from_user.username or m.from_user.first_name or '匿名'
        cursor.execute("""
            INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (chat,user,name,amt,rate,fee,comm,cur,dt,m.message_id))
        conn.commit()
        after = ceil2(amt*(1-fee/100))
        usdt = ceil2(after/rate) if rate else 0
        comm_amt = ceil2(amt*(comm/100))
        rep = f"✅ 已入款 {amt}\n编号：{m.message_id:03d}\n"
        rep += f"{fmt_time(dt)} {amt}*{1-fee/100:.2f}/{rate} = {usdt}  {name}\n"
        if comm>0:
            rep += f"{fmt_time(dt)} {amt}*{comm/100:.3f} = {comm_amt} 【佣金】\n"
        rep += show_summary(chat, user)
        bot.reply_to(m, rep)
    else:
        cursor.execute(
            "SELECT id FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id DESC LIMIT 1",
            (chat,user)
        )
        rec = cursor.fetchone()
        if rec:
            tid = rec['id']
            cursor.execute("DELETE FROM transactions WHERE id=%s", (tid,))
            conn.commit()
            bot.reply_to(m, f"✅ 删除订单成功，编号：{tid:03d}")
        else:
            bot.reply_to(m, "⚠️ 暂无可删订单。")

@bot.message_handler(func=lambda m: m.text and m.text.startswith('删除订单'))
def delete_by_id(m):
    parts = m.text.strip().split()
    if len(parts)!=2 or not parts[1].isdigit():
        return bot.reply_to(m, "❌ 格式：删除订单 <编号>")
    tid = int(parts[1])
    chat,user = m.chat.id, m.from_user.id
    cursor.execute(
        "DELETE FROM transactions WHERE id=%s AND chat_id=%s AND user_id=%s",
        (tid,chat,user)
    )
    if cursor.rowcount:
        conn.commit()
        bot.reply_to(m, f"✅ 删除订单成功，编号：{tid:03d}")
    else:
        bot.reply_to(m, "⚠️ 未找到该编号。")

@bot.message_handler(func=lambda m: m.text in ['📊 汇总','汇总'])
def cmd_summary(m):
    chat,user = m.chat.id, m.from_user.id
    st = get_settings(chat,user)
    if not st:
        return bot.reply_to(m, "❌ 请先 /start → 设置交易")
    bot.reply_to(m, show_summary(chat,user))

@bot.message_handler(func=lambda m: m.text in ['🛠️ 帮助','帮助'])
def cmd_help(m):
    bot.reply_to(m,
        "/start — 开始\n"
        "/trade — 设置交易\n"
        "+1000 / -1000 — 入/删\n"
        "删除订单 001 — 按编号删\n"
        "汇总 — 查看汇总"
    )

bot.remove_webhook()
bot.infinity_polling()
