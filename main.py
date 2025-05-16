import os
import re
import pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telebot import TeleBot, types

# —— 配置 —— #
TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 初始化建表 —— #
def init_db():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
      chat_id BIGINT, user_id BIGINT,
      rate DOUBLE PRECISION, fee_rate DOUBLE PRECISION, commission_rate DOUBLE PRECISION,
      PRIMARY KEY(chat_id,user_id)
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS transactions (
      id SERIAL PRIMARY KEY,
      chat_id BIGINT, user_id BIGINT,
      amount DOUBLE PRECISION, rate DOUBLE PRECISION,
      fee_rate DOUBLE PRECISION, commission_rate DOUBLE PRECISION,
      date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      status TEXT DEFAULT 'pending'
    );
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS issuances (
      id SERIAL PRIMARY KEY,
      chat_id BIGINT, user_id BIGINT,
      amount DOUBLE PRECISION, date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      type TEXT
    );
    """)
    conn.commit()

init_db()

# —— 工具函数 —— #
def fetch_settings(cid, uid):
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s", (cid, uid))
    return cursor.fetchone()

def fetch_transactions(cid, uid):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date", (cid, uid)
    )
    return cursor.fetchall()

def fetch_issuances(cid, uid):
    cursor.execute(
        "SELECT * FROM issuances WHERE chat_id=%s AND user_id=%s ORDER BY date", (cid, uid)
    )
    return cursor.fetchall()

# —— 定义格式化汇总 —— #
def format_summary(cid, uid):
    tz = pytz.timezone('Asia/Kuala_Lumpur')
    today = datetime.now(tz).date()

    trans = fetch_transactions(cid, uid)
    issu  = fetch_issuances(cid, uid)

    pending_lines = []
    deleted_lines = []
    for r in trans:
        dt = r['date']
        if dt is None:
            continue
        # 处理时区：若无 tzinfo，当成 UTC
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        local_dt = dt.astimezone(tz)
        if local_dt.date() != today:
            continue
        sign = '-' if r['status']=='deleted' else '+'
        amt  = r['amount']
        ts   = local_dt.strftime('%H:%M:%S')
        netf = 1 - r['fee_rate']/100
        usd  = round(amt*netf/r['rate'],2)
        line = f"{r['id']:03d}. {ts} {sign}{abs(amt)} * {netf:.2f} / {r['rate']:.1f} = {usd:.2f}"
        if r['status']=='pending':
            pending_lines.append(line)
        else:
            deleted_lines.append(line)

    out_lines = []
    for r in issu:
        dt = r['date']
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = pytz.utc.localize(dt)
        local_dt = dt.astimezone(tz)
        if local_dt.date() != today:
            continue
        sign = '' if r['amount']>=0 else '-'
        ts   = local_dt.strftime('%H:%M:%S')
        out_amt = abs(r['amount'])
        out_lines.append(f"{ts} {sign}{out_amt:.2f}")

    # 汇总计算
    total_in = sum(r['amount'] for r in trans if r['status']=='pending')
    comm_due = sum(r['amount']*r['commission_rate']/100 for r in trans if r['status']=='pending')
    total_pending = sum(r['amount']*(1-r['fee_rate']/100) for r in trans if r['status']=='pending')
    issued_amt = sum(r['amount'] for r in issu if r['type']=='fund')
    comm_issued= sum(r['amount'] for r in issu if r['type']=='commission')
    unissued = total_pending - issued_amt

    s = fetch_settings(cid, uid) or {'rate':0,'fee_rate':0,'commission_rate':0}
    rate = s['rate']
    fee  = s['fee_rate']
    comm = s['commission_rate']

    lines = []
    lines.append(f"今日入笔（{len(pending_lines)}笔）")
    lines += pending_lines + deleted_lines
    lines.append("")
    lines.append(f"今日下发（{len(out_lines)}笔）")
    lines += out_lines
    lines.append("")
    lines.append(f"已入款（{len(pending_lines)}笔）：{total_in:.1f} (RMB)")
    lines.append(f"汇率：{rate:.1f}")
    lines.append(f"费率：{fee:.1f}%")
    lines.append(f"佣金：{comm_due:.1f} | {comm_due/rate:.2f} USDT")
    lines.append("")
    lines.append(f"应下发：{total_pending:.2f} | {total_pending/rate:.2f} (USDT)")
    lines.append(f"已下发：{issued_amt:.2f} | {issued_amt/rate:.2f} (USDT)")
    lines.append(f"未下发：{unissued:.2f} | {unissued/rate:.2f} (USDT)")
    lines.append("")
    lines.append(f"佣金应下发：{comm_due:.2f} | {comm_due/rate:.2f} (USDT)")
    lines.append(f"佣金已下发：{comm_issued:.2f} | {comm_issued/rate:.2f} (USDT)")
    lines.append(f"佣金未下发：{comm_due-comm_issued:.2f} | {(comm_due-comm_issued)/rate:.2f} (USDT)")
    return "\n".join(lines)

# —— 入账 —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    s = fetch_settings(cid,uid)
    if not s: return bot.reply_to(msg, "❌ 请先 /trade 设置交易参数")
    amt = float(re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)[0])
    cursor.execute(
        "INSERT INTO transactions(chat_id,user_id,amount,rate,fee_rate,commission_rate)"
        "VALUES(%s,%s,%s,%s,%s,%s)",
        (cid,uid,amt,s['rate'],s['fee_rate'],s['commission_rate'])
    )
    conn.commit()
    bot.reply_to(msg, "✅ 已入款 +{:.1f} (RMB)\n\n".format(amt) + format_summary(cid,uid))

# —— 删除最近一笔 —— #
@bot.message_handler(func=lambda m: re.match(r'^(删除|撤销入款)\d+(\.\d+)?$', m.text or ''))
def handle_delete(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    row = fetch_transactions(cid,uid)[-1] if fetch_transactions(cid,uid) else None
    if not row: return bot.reply_to(msg, "❌ 无可删除的入账记录")
    cursor.execute("UPDATE transactions SET status='deleted' WHERE id=%s", (row['id'],))
    conn.commit()
    bot.reply_to(msg, f"✅ 订单{row['id']:03d} 已删除 -{row['amount']:.1f} (RMB)\n\n" + format_summary(cid,uid))

# —— 下发 —— #
@bot.message_handler(func=lambda m: re.match(r'^下发-?\d+(\.\d+)?$', m.text or ''))
def handle_issuance(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    val = float(msg.text.replace('下发',''))
    cursor.execute(
        "INSERT INTO issuances(chat_id,user_id,amount,type) VALUES(%s,%s,%s,'fund')",
        (cid,uid,val)
    )
    conn.commit()
    bot.reply_to(msg, "✅ 已记录下发 {:+.2f} USDT\n\n".format(val) + format_summary(cid,uid))

# —— 佣金下发 —— #
@bot.message_handler(func=lambda m: re.match(r'^佣金下发-?\d+(\.\d+)?$', m.text or ''))
def handle_comm_issuance(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    val = float(msg.text.replace('佣金下发',''))
    cursor.execute(
        "INSERT INTO issuances(chat_id,user_id,amount,type) VALUES(%s,%s,%s,'commission')",
        (cid,uid,val)
    )
    conn.commit()
    bot.reply_to(msg, "✅ 已记录佣金下发 {:+.2f} USDT\n\n".format(val) + format_summary(cid,uid))

# —— 显示账单 —— #
@bot.message_handler(commands=['显示账单'])
def cmd_summary(msg):
    bot.reply_to(msg, format_summary(msg.chat.id,msg.from_user.id))

# —— 客服帮助 & 定制 —— #
@bot.message_handler(commands=['客服帮助'])
def cmd_cs(msg):
    bot.reply_to(msg, "联系客服：<客服链接>")

@bot.message_handler(commands=['定制机器人'])
def cmd_custom(msg):
    bot.reply_to(msg, "定制请见：<定制链接>")

if __name__=='__main__':
    bot.remove_webhook()
    bot.infinity_polling(skip_pending=True)
