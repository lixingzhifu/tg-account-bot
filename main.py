import os
import re
import math
import telebot
import psycopg2
from datetime import datetime, timezone, timedelta
from psycopg2.extras import RealDictCursor

# 环境变量
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# 初始化 Bot 和 数据库
bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 重建表（部署或重启时执行一次）
cursor.execute('DROP TABLE IF EXISTS transactions;')
cursor.execute('DROP TABLE IF EXISTS settings;')

cursor.execute('''
CREATE TABLE settings (
    chat_id         BIGINT PRIMARY KEY,
    currency        TEXT    NOT NULL DEFAULT 'RMB',
    rate            DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee_rate        DOUBLE PRECISION NOT NULL DEFAULT 0,
    commission_rate DOUBLE PRECISION NOT NULL DEFAULT 0
);
''')
cursor.execute('''
CREATE TABLE transactions (
    id          SERIAL PRIMARY KEY,
    chat_id     BIGINT NOT NULL,
    name        TEXT   NOT NULL,
    amount      DOUBLE PRECISION NOT NULL,
    date        TIMESTAMP NOT NULL,
    message_id  BIGINT
);
''')
conn.commit()

# 工具函数
def ceil2(x):
    return math.ceil(x * 100) / 100.0

def now_bj():
    return datetime.now(timezone(timedelta(hours=8)))

def get_settings(chat_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s',
        (chat_id,)
    )
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

def set_settings(chat_id, currency, rate, fee, comm):
    cursor.execute('''
        UPDATE settings
           SET currency=%s, rate=%s, fee_rate=%s, commission_rate=%s
         WHERE chat_id=%s
    ''', (currency, rate, fee, comm, chat_id))
    if cursor.rowcount == 0:
        cursor.execute('''
            INSERT INTO settings(chat_id,currency,rate,fee_rate,commission_rate)
            VALUES(%s,%s,%s,%s,%s)
        ''', (chat_id, currency, rate, fee, comm))
    conn.commit()

def build_summary(chat_id):
    c, r, f, cm = get_settings(chat_id)
    cursor.execute(
        'SELECT * FROM transactions WHERE chat_id=%s ORDER BY date',
        (chat_id,)
    )
    rows = cursor.fetchall()
    total = sum(rw['amount'] for rw in rows)
    after_fee_rmb = ceil2(total * (1 - f/100))
    send_usdt    = ceil2(after_fee_rmb / r)     if r else 0
    comm_rmb     = ceil2(total * cm/100)
    comm_usdt    = ceil2(comm_rmb / r)          if r else 0

    lines = []
    for i, rw in enumerate(rows, 1):
        t = rw['date'].strftime('%H:%M:%S')
        usdt = ceil2(rw['amount'] * (1 - f/100) / r) if r else 0
        lines.append(f"{i:03d}. {t}  {rw['amount']}×{(1 - f/100):.2f}/{r} = {usdt}  {rw['name']}")
    footer = (
        f"\n已入款（{len(rows)}笔）：{total} ({c})\n"
        f"总入款金额：{total} ({c})\n汇率：{r}\n费率：{f}%\n佣金：{cm}%\n\n"
        f"应下发：{after_fee_rmb}({c}) | {send_usdt} (USDT)\n"
        f"已下发：0.0({c}) | 0.0 (USDT)\n"
        f"未下发：{after_fee_rmb}({c}) | {send_usdt} (USDT)\n"
    )
    if cm > 0:
        footer += f"\n中介佣金应下发：{comm_rmb}({c}) | {comm_usdt} (USDT)"
    return "\n".join(lines) + "\n" + footer

# /start 菜单
@bot.message_handler(commands=['start'])
def on_start(msg):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易','📘 指令大全')
    kb.row('🔁 清零记录','📊 汇总')
    bot.send_message(msg.chat.id, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

# 显示设置模板
@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def on_show_trade(m):
    bot.reply_to(m,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

# 解析并保存设置
@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def on_set_trade(m):
    Ls = m.text.replace('：',':').splitlines()
    data = {'货币':None,'汇率':None,'费率':None,'中介佣金':None}
    errs = []
    for L in Ls:
        L = L.replace(' ','').strip()
        for k in data:
            if L.startswith(k):
                if k=='货币':
                    data[k] = L.split(':',1)[1] or 'RMB'
                else:
                    try:
                        data[k] = float(re.findall(r'\d+\.?\d*',L)[0])
                    except:
                        errs.append(f"{k}格式错误")
    if errs or data['汇率'] is None:
        bot.reply_to(m, "设置错误\n" + ("\n".join(errs) if errs else "缺少汇率"))
        return
    set_settings(m.chat.id, data['货币'], data['汇率'], data['费率'], data['中介佣金'])
    bot.reply_to(m,
        "✅ 设置成功\n"
        f"设置货币：{data['货币']}\n"
        f"设置汇率：{data['汇率']}\n"
        f"设置费率：{data['费率']}%\n"
        f"中介佣金：{data['中介佣金']}%"
    )

# 记入 (+) 或 删除 (−)
@bot.message_handler(func=lambda m: re.match(r'^([+\-]|加|减)\s*\d+(\.\d+)?', m.text or ''))
def on_amount(m):
    txt = m.text.strip()
    op  = '+' if txt[0] in '+加' else '-'
    num = float(re.findall(r'\d+\.?\d*', txt)[0])
    cid = m.chat.id

    if op=='-':
        cursor.execute(
            "DELETE FROM transactions WHERE chat_id=%s ORDER BY id DESC LIMIT 1",
            (cid,)
        )
        conn.commit()
        return bot.reply_to(m, "🗑 已删除一笔记录")

    name = m.from_user.username or m.from_user.first_name or '匿名'
    cursor.execute('''
        INSERT INTO transactions(chat_id,name,amount,date,message_id)
        VALUES(%s,%s,%s,%s,%s) RETURNING id
    ''', (cid, name, num, now_bj(), m.message_id))
    new_id = cursor.fetchone()['id']
    conn.commit()

    bot.reply_to(m,
        f"✅ 已入款 +{num}\n编号：{new_id:03d}\n" + build_summary(cid)
    )

# 清零记录
@bot.message_handler(func=lambda m: m.text in ['🔁 清零记录','/reset'])
def on_reset(m):
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s", (m.chat.id,))
    conn.commit()
    bot.reply_to(m, "🔄 已清空记录")

# 汇总
@bot.message_handler(func=lambda m: m.text in ['📊 汇总','/summary'])
def on_summary(m):
    bot.reply_to(m, build_summary(m.chat.id))

# 指令大全
@bot.message_handler(func=lambda m: m.text in ['📘 指令大全','/help'])
def on_help(m):
    bot.reply_to(m,
        "📋 指令大全\n"
        "/start — 显示菜单\n"
        "设置交易 — 进入参数设置\n"
        "+1000 — 记入款 1000\n"
        "-1000 — 删除最近一笔\n"
        "🔁 清零记录 — 清空记录\n"
        "📊 汇总 — 查看汇总"
    )

# 启动
bot.remove_webhook()
bot.infinity_polling()
