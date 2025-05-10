import os
import re
import math
from datetime import datetime, timedelta

import telebot
import psycopg2
from psycopg2.extras import RealDictCursor

# ———— 配置 ————
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

bot = telebot.TeleBot(TOKEN)

# ———— 数据库初始化 ————
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)
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
)
""")
conn.commit()

# ———— 工具函数 ————
def now_malaysia():
    # 服务器用 UTC，这里 +8h 得到马来西亚时间
    return datetime.utcnow() + timedelta(hours=8)

def ceil2(x):
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if not row or row['rate'] == 0:
        return None
    return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']

def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    recs = cursor.fetchall()
    total = sum(r['amount'] for r in recs)
    currency, rate, fee, comm = get_settings(chat_id, user_id)
    # 汇总文字
    lines = []
    for i, r in enumerate(recs, 1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = r['amount'] * (1 - r['fee_rate']/100)
        usdt = ceil2(after_fee / r['rate']) if r['rate'] else 0
        lines.append(f"{i}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usdt}  {r['name']}")
        if r['commission_rate']>0:
            cm = ceil2(r['amount']*r['commission_rate']/100)
            lines.append(f"{i}. {t} {r['amount']}*{r['commission_rate']/100:.3f} = {cm} 【佣金】")
    summary = "\n".join(lines)

    converted_total = ceil2(total*(1-fee/100)/rate)
    commission_rmb = ceil2(total*comm/100)
    commission_usdt = ceil2(commission_rmb/rate)
    reply = (
        f"已入款（{len(recs)}笔）：{total} ({currency})\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{comm}%\n\n"
        f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total}(USDT)\n"
        f"已下发：0.0({currency}) | 0.0(USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted_total}(USDT)\n"
    )
    if comm>0:
        reply += f"\n中介佣金应下发：{commission_rmb}({currency}) | {commission_usdt}(USDT)"
    return summary + "\n\n" + reply

# ———— 命令与消息处理 ————
@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📊 汇总')
    kb.row('❓ 帮助', '/trade')
    bot.send_message(msg.chat.id, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

@bot.message_handler(commands=['id'])
def cmd_id(msg):
    bot.reply_to(msg, f"chat_id：{msg.chat.id}\nuser_id：{msg.from_user.id}")

@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def cmd_trade(msg):
    bot.reply_to(
        msg,
        "请按以下格式发送：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：9\n"
        "设置费率：2\n"
        "中介佣金：0.5"
    )

@bot.message_handler(func=lambda m: '设置交易指令' in m.text)
def set_trade(msg):
    chat, user = msg.chat.id, msg.from_user.id
    lines = [l.strip() for l in msg.text.replace('：',':').splitlines()]
    data = {'currency':None,'rate':None,'fee':0,'comm':0}
    for L in lines:
        if L.startswith('设置货币:'):
            data['currency']=L.split(':',1)[1].strip().upper()
        if L.startswith('设置汇率:'):
            try: data['rate']=float(L.split(':',1)[1])
            except: pass
        if L.startswith('设置费率:'):
            try: data['fee']=float(L.split(':',1)[1])
            except: pass
        if L.startswith('中介佣金:'):
            try: data['comm']=float(L.split(':',1)[1])
            except: pass
    if data['rate'] is None:
        bot.reply_to(msg, "❌ 请至少填写 汇率")
        return
    cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
           currency=EXCLUDED.currency,
           rate=EXCLUDED.rate,
           fee_rate=EXCLUDED.fee_rate,
           commission_rate=EXCLUDED.commission_rate
    """, (chat,user,data['currency'],data['rate'],data['fee'],data['comm']))
    conn.commit()
    bot.reply_to(
        msg,
        f"✅ 设置成功\n货币：{data['currency']}\n汇率：{data['rate']}\n费率：{data['fee']}%\n佣金：{data['comm']}%"
    )

@bot.message_handler(commands=['trade'])
def slash_trade(msg):
    # 让 /trade 和菜单效果一致
    cmd_trade(msg)

@bot.message_handler(func=lambda m: re.match(r'^[\+\-].+', m.text))
def handle_amount(msg):
    chat, user = msg.chat.id, msg.from_user.id
    cfg = get_settings(chat,user)
    if not cfg:
        bot.reply_to(msg, "⚠️ 请先用「设置交易」填写汇率后再入笔")
        return
    cur, rate, fee, comm = cfg
    txt = msg.text.strip()
    sign = +1 if txt[0]=='+' else -1
    amt = float(re.findall(r'\d+\.?\d*', txt)[0]) * sign
    name = msg.from_user.username or msg.from_user.first_name or '匿名'
    now = now_malaysia()
    cursor.execute("""
        INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat,user,name,amt,rate,fee,comm,cur,now,msg.message_id))
    conn.commit()
    # 反馈
    total_cnt = cursor.execute("SELECT COUNT(*) FROM transactions WHERE chat_id=%s AND user_id=%s", (chat,user))
    # 事务编号用自增 id，或者自己用 COUNT+1
    cursor.execute("SELECT MAX(id) FROM transactions WHERE chat_id=%s AND user_id=%s", (chat,user))
    last_id = cursor.fetchone()['max'] or 0
    t = now.strftime('%d-%m-%Y %H:%M:%S')
    after_fee = amt*(1-fee/100)
    usdt = ceil2(after_fee/rate)
    fee_amt = ceil2(amt*fee/100)
    comm_amt = ceil2(abs(amt)*comm/100)
    reply = [
        f"✅ 已入款 {amt} ({cur})" if sign>0 else f"🗑️ 已删除 {amt} ({cur})",
        f"编号：{last_id:03d}",
        f"1. {t} {amt}*{1-fee/100:.3f}/{rate} = {usdt}  {name}"
    ]
    if comm>0:
        reply.append(f"2. {t} {amt}*{comm/100:.3f} = {comm_amt} 【佣金】")
    reply.append("\n"+ show_summary(chat,user))
    bot.reply_to(msg, "\n".join(reply))

# 启动
bot.remove_webhook()
bot.infinity_polling()
