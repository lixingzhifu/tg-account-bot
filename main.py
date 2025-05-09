import os
import re
import math
import telebot
import psycopg2
from datetime import datetime
from psycopg2.extras import RealDictCursor

# — 环境变量 —
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# — 初始化 Bot & DB —
bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# — 彻底重置表，重建最新结构 —
cursor.execute('DROP TABLE IF EXISTS transactions;')
cursor.execute('DROP TABLE IF EXISTS settings;')

cursor.execute('''
CREATE TABLE settings (
    chat_id BIGINT PRIMARY KEY,
    currency TEXT NOT NULL DEFAULT 'RMB',
    rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee_rate DOUBLE PRECISION NOT NULL DEFAULT 0,
    commission_rate DOUBLE PRECISION NOT NULL DEFAULT 0
);
''')
cursor.execute('''
CREATE TABLE transactions (
    id SERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    amount DOUBLE PRECISION NOT NULL,
    date TIMESTAMP NOT NULL,
    message_id BIGINT
);
''')
conn.commit()

# — 工具函数 —
def ceil2(x): return math.ceil(x * 100) / 100.0

def get_settings(chat_id):
    cursor.execute(
        'SELECT currency, rate, fee_rate, commission_rate FROM settings WHERE chat_id=%s',
        (chat_id,)
    )
    row = cursor.fetchone()
    return (row['currency'], row['rate'], row['fee_rate'], row['commission_rate']) if row else ('RMB', 0, 0, 0)

def set_settings(chat_id, currency, rate, fee, commission):
    cursor.execute('''
        UPDATE settings
           SET currency=%s, rate=%s, fee_rate=%s, commission_rate=%s
         WHERE chat_id=%s
    ''', (currency, rate, fee, commission, chat_id))
    if cursor.rowcount == 0:
        cursor.execute('''
            INSERT INTO settings(chat_id, currency, rate, fee_rate, commission_rate)
            VALUES(%s, %s, %s, %s, %s)
        ''', (chat_id, currency, rate, fee, commission))
    conn.commit()

def build_summary(chat_id):
    cursor.execute(
        'SELECT * FROM transactions WHERE chat_id=%s ORDER BY date',
        (chat_id,)
    )
    rows = cursor.fetchall()
    currency, rate, fee, commission = get_settings(chat_id)
    total = sum(r['amount'] for r in rows)
    converted = ceil2(total * (1 - fee/100) / rate) if rate else 0
    comm_rmb = ceil2(total * commission/100)
    comm_usdt = ceil2(comm_rmb / rate) if rate else 0

    lines = []
    for idx, r in enumerate(rows, 1):
        t = r['date'].strftime('%H:%M:%S')
        after_fee = ceil2(r['amount'] * (1 - fee/100))
        usdt = ceil2(after_fee / rate) if rate else 0
        lines.append(f"{idx}. {t} {r['amount']}*{(1 - fee/100):.2f}/{rate} = {usdt}  {r['name']}")
        if commission > 0:
            cm = ceil2(r['amount'] * commission/100)
            lines.append(f"{idx}. {t} {r['amount']}*{commission/100:.2f} = {cm} 【佣金】")

    footer = (
        f"\n已入款（{len(rows)}笔）：{total} ({currency})\n"
        f"总入款金额：{total} ({currency})\n汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{ceil2(total*(1-fee/100))}({currency}) | {converted} (USDT)\n"
        f"已下发：0.0({currency}) | 0.0 (USDT)\n"
        f"未下发：{ceil2(total*(1-fee/100))}({currency}) | {converted} (USDT)\n"
    )
    if commission > 0:
        footer += f"\n中介佣金应下发：{comm_rmb}({currency}) | {comm_usdt} (USDT)"
    return "\n".join(lines) + "\n\n" + footer

# — /start & 菜单 —
@bot.message_handler(commands=['start'])
def on_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易','📘 指令大全')
    kb.row('🔁 计算重启','📊 汇总')
    kb.row('❓ 需要帮助','🛠️ 定制机器人')
    bot.send_message(m.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请从下方菜单选择操作：",
        reply_markup=kb
    )

# — 查看 chat_id (测试用) —
@bot.message_handler(commands=['id'])
def on_id(m):
    bot.reply_to(m, f"chat_id: {m.chat.id}")

# — 显示设置模板 —
@bot.message_handler(func=lambda m: m.text in ['设置交易','💱 设置交易'])
def on_show_trade(m):
    bot.reply_to(m,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

# — 解析并保存设置 —
@bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
def on_set_trade(m):
    lines = m.text.replace('：',':').strip().splitlines()
    data = {'currency':None,'rate':None,'fee':None,'comm':None}
    errs = []
    for L in lines:
        L = L.replace(' ', '')
        if L.startswith('设置货币'):
            data['currency'] = L.split(':',1)[1] or 'RMB'
        elif L.startswith('设置汇率'):
            try: data['rate'] = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('汇率格式错误')
        elif L.startswith('设置费率'):
            try: data['fee'] = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('费率格式错误')
        elif L.startswith('中介佣金'):
            try: data['comm'] = float(re.findall(r'\d+\.?\d*',L)[0])
            except: errs.append('中介佣金格式错误')
    if errs or data['rate'] is None:
        bot.reply_to(m, "设置错误\n" + ("\n".join(errs) if errs else "缺少汇率"))
        return
    set_settings(m.chat.id, data['currency'], data['rate'], data['fee'], data['comm'])
    bot.reply_to(m,
        f"✅ 设置成功\n"
        f"设置货币：{data['currency']}\n"
        f"设置汇率：{data['rate']}\n"
        f"设置费率：{data['fee']}%\n"
        f"中介佣金：{data['comm']}%"
    )

# — 入笔 / 删除 —
@bot.message_handler(func=lambda m: re.match(r'^([+\-]|加|减)\s*\d+(\.\d+)?', m.text or ''))
def on_amount(m):
    txt = m.text.strip()
    op = '+' if txt[0] in '+加' else '-'
    num = float(re.findall(r'\d+\.?\d*', txt)[0])
    cid = m.chat.id
    if op == '-':
        cursor.execute("DELETE FROM transactions WHERE chat_id=%s ORDER BY id DESC LIMIT 1", (cid,))
        conn.commit()
        return bot.reply_to(m, f"🗑 已删除 {num}")
    # 正常入笔
    name = m.from_user.username or m.from_user.first_name or '匿名'
    cursor.execute('''
        INSERT INTO transactions(chat_id,name,amount,date,message_id)
        VALUES(%s,%s,%s,%s,%s)
    ''', (cid, name, num, datetime.now(), m.message_id))
    conn.commit()
    bot.reply_to(m,
        f"✅ 已入款 +{num}\n编号：{m.message_id}\n" + build_summary(cid)
    )

# — 指令大全 —
@bot.message_handler(func=lambda m: m.text in ['📘 指令大全','指令大全'])
def on_commands(m):
    bot.reply_to(m,
        "/start - 显示菜单\n"
        "设置交易 - 进入参数设置\n"
        "📘 指令大全 - 帮助列表\n"
        "🔁 计算重启 - 清空记录\n"
        "📊 汇总 - 查看当日汇总\n"
        "+1000 - 记入款\n"
        "-1000 - 删除最近一笔"
    )

# — 清空记录 —
@bot.message_handler(func=lambda m: m.text in ['🔁 计算重启','/reset'])
def on_reset(m):
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s", (m.chat.id,))
    conn.commit()
    bot.reply_to(m, "🔄 已清空所有记录")

# — 汇总 —
@bot.message_handler(func=lambda m: m.text in ['📊 汇总','/summary'])
def on_summary(m):
    bot.reply_to(m, build_summary(m.chat.id))

# — 启动 Bot —
bot.remove_webhook()
bot.infinity_polling()
