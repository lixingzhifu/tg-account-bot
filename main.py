# main.py
import os
import re
import math
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
import pytz
import telebot

# —— 配置 —— #
TOKEN        = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')
# 马来西亚时区
TZ = pytz.timezone('Asia/Kuala_Lumpur')

bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 初始化表 —— #
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY(chat_id, user_id)
)""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT,
    user_id BIGINT,
    amount DOUBLE PRECISION,
    rate DOUBLE PRECISION,
    fee_rate DOUBLE PRECISION,
    commission_rate DOUBLE PRECISION,
    currency TEXT,
    date TIMESTAMP,
    message_id BIGINT
)""")
conn.commit()

# —— 工具函数 —— #
def fmt2(v):  # 保留两位小数
    return math.floor(v*100)/100

def get_settings(chat, user):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat, user)
    )
    row = cursor.fetchone()
    if not row:
        return 'RMB', 0, 0, 0
    return row['currency'], row['rate'], row['fee_rate'], row['commission_rate']

def show_summary(chat, user):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat, user)
    )
    recs = cursor.fetchall()
    total = sum(r['amount'] for r in recs)
    cur, rate, fee, comm = get_settings(chat, user)
    # 计算应下发和佣金
    net_rmb = fmt2(total*(1 - fee/100))
    net_usdt = fmt2(net_rmb/rate) if rate else 0
    comm_rmb = fmt2(total*(comm/100))
    comm_usdt = fmt2(comm_rmb/rate) if rate else 0

    # 汇总输出
    lines = [f"已入款（{len(recs)}笔）：{total} ({cur})",
             f"总入款金额：{total} ({cur})",
             f"汇率：{rate}",
             f"费率：{fee}%",
             f"佣金：{comm}%\n",
             f"应下发：{net_rmb}({cur}) | {net_usdt} (USDT)",
             f"已下发：0.0({cur}) | 0.0 (USDT)",
             f"未下发：{net_rmb}({cur}) | {net_usdt} (USDT)"]
    if comm>0:
        lines.append(f"\n中介佣金应下发：{comm_rmb}({cur}) | {comm_usdt} (USDT)")
    return "\n".join(lines)

def format_time(dt):
    return dt.astimezone(TZ).strftime('%d-%m-%Y %H:%M:%S')

def next_order_id(chat, user):
    cursor.execute(
        "SELECT LPAD(COALESCE(MAX(id),0)+1::text,3,'0') AS next_id "
        "FROM transactions WHERE chat_id=%s AND user_id=%s",
        (chat, user)
    )
    return cursor.fetchone()['next_id']

# —— 处理 Start/Help/ID —— #
@bot.message_handler(commands=['start','help'])
def cmd_start(msg):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row('💱 设置交易','📊 汇总')
    markup.row('+ 入笔','🗑️ 删除订单')
    bot.send_message(
        msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n"
        "请先 /trade 设置交易参数，然后使用下方按钮或命令操作。",
        reply_markup=markup
    )

@bot.message_handler(commands=['id'])
def cmd_id(msg):
    bot.reply_to(msg, f"chat_id：{msg.chat.id}\nuser_id：{msg.from_user.id}")

# —— 设置交易 —— #
@bot.message_handler(commands=['trade'])
@bot.message_handler(func=lambda m: m.text and m.text.strip() in ['设置交易','💱 设置交易'])
def cmd_trade(msg):
    bot.reply_to(msg,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def handle_trade_config(msg):
    chat, user = msg.chat.id, msg.from_user.id
    text = msg.text.replace('：',':').upper()
    cur = rate = fee = comm = None
    errs = []
    for L in text.split('\n'):
        L2 = L.replace(' ','')
        if '货币' in L2:
            cur = re.sub(r'[^A-Z]','',L2.split(':',1)[1])
        if '汇率' in L2:
            try: rate = float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append("汇率格式错误")
        if '费率' in L2:
            try: fee = float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append("费率格式错误")
        if '中介佣金' in L2 or '佣金' in L2:
            try: comm = float(re.findall(r'\d+\.?\d*',L2)[0])
            except: errs.append("佣金格式错误")
    if errs:
        return bot.reply_to(msg,"设置错误\n"+'\n'.join(errs))
    if rate is None:
        return bot.reply_to(msg,"⚠️ 至少需要提供 汇率")
    # 写入 DB
    cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
    """, (chat,user,cur or 'RMB',rate,fee or 0,comm or 0))
    conn.commit()
    bot.reply_to(msg,
        "✅ 设置成功\n"
        f"设置货币：{cur or 'RMB'}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee or 0}%\n"
        f"中介佣金：{comm or 0}%"
    )

# —— 入笔 (+1000) —— #
@bot.message_handler(regexp=r'^[+＋]\s*\d+(\.\d+)?$')
def handle_add(msg):
    chat,user = msg.chat.id,msg.from_user.id
    # 权限检查：群里非管理员不允许
    if msg.chat.type != 'private':
        member = bot.get_chat_member(chat, user)
        if not (member.status in ['creator','administrator']):
            return bot.reply_to(msg,"⚠️ 你没有权限入笔，请联系群管理员。")
    amt = float(re.findall(r'\d+(\.\d+)?', msg.text)[0])
    cur,rate,fee,comm = get_settings(chat,user)
    if rate==0:
        return bot.reply_to(msg,"⚠️ 请先用 /trade 设置汇率，然后再入笔。")
    now = datetime.now(TZ)
    oid = next_order_id(chat,user)
    cursor.execute("""
        INSERT INTO transactions
        (chat_id,user_id,amount,rate,fee_rate,commission_rate,currency,date,message_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat,user,amt,rate,fee,comm,cur,now,msg.message_id))
    conn.commit()

    # 单笔回执
    net = fmt2(amt*(1-fee/100))
    usdt = fmt2(net/rate)
    comm_amt = fmt2(amt*(comm/100))
    reply = [
        f"✅ 已入款 +{amt} ({cur})",
        f"编号：{oid}",
        f"{format_time(now)}  {amt}*{(1-fee/100):.2f}/{rate} = {usdt}  @{msg.from_user.username or msg.from_user.id}",
    ]
    if comm>0:
        reply.append(f"{format_time(now)}  {amt}*{comm/100:.3f} = {comm_amt} 【佣金】")
    reply.append("")
    reply.extend(show_summary(chat,user).split("\n"))
    bot.reply_to(msg,"\n".join(reply))

# —— 删除最近一笔 (“-1000”) —— #
@bot.message_handler(regexp=r'^[-－]\s*\d+(\.\d+)?$')
def handle_delete_last(msg):
    chat,user = msg.chat.id,msg.from_user.id
    # 同样权限检查
    if msg.chat.type!='private':
        member=bot.get_chat_member(chat,user)
        if member.status not in ['creator','administrator']:
            return bot.reply_to(msg,"⚠️ 你没有权限删除订单。")
    # 删最新一条
    cursor.execute("""
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s
        ORDER BY id DESC
        LIMIT 1
    """,(chat,user))
    if cursor.rowcount:
        conn.commit()
        bot.reply_to(msg,"✅ 删除最近一笔成功。")
    else:
        bot.reply_to(msg,"⚠️ 没有可删除的订单。")

# —— 删除指定编号 （“删除订单 001”） —— #
@bot.message_handler(func=lambda m: m.text and re.match(r'^删除订单\s*\d{3}$',m.text))
def handle_delete_one(msg):
    chat,user = msg.chat.id,msg.from_user.id
    if msg.chat.type!='private':
        member=bot.get_chat_member(chat,user)
        if member.status not in ['creator','administrator']:
            return bot.reply_to(msg,"⚠️ 你没有权限删除订单。")
    oid = msg.text.strip().split()[-1]
    # 把 001 → 找到对应那条
    cursor.execute("""
      DELETE FROM transactions
      WHERE chat_id=%s AND user_id=%s
        AND LPAD(id::text,3,'0')=%s
    """,(chat,user,oid))
    if cursor.rowcount:
        conn.commit()
        bot.reply_to(msg,f"✅ 删除订单成功，编号：{oid}")
    else:
        bot.reply_to(msg,"⚠️ 未找到该编号订单。")

# —— 汇总命令 —— #
@bot.message_handler(func=lambda m: m.text and m.text.strip() in ['汇总','📊 汇总'])
def cmd_summary(msg):
    chat,user = msg.chat.id, msg.from_user.id
    bot.reply_to(msg, show_summary(chat,user))

# —— 启动 —— #
bot.remove_webhook()
bot.infinity_polling()
