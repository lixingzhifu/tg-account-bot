from keep_alive import keep_alive
import telebot
import sqlite3
from datetime import datetime
import math
import re

import os
TOKEN = os.getenv('TOKEN')
bot = telebot.TeleBot(TOKEN)

# 初始化数据库
conn = sqlite3.connect('transactions.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id INTEGER PRIMARY KEY,
    rate REAL DEFAULT 0,
    fee_rate REAL DEFAULT 0,
    commission_rate REAL DEFAULT 0
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS transactions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    amount_cny REAL,
    rate REAL,
    fee_rate REAL,
    commission_rate REAL,
    amount_usdt REAL,
    fee_usdt REAL,
    commission_usdt REAL,
    final_usdt REAL,
    status TEXT,
    date TEXT
)
''')
conn.commit()

def ceil2(n):
    return math.ceil(n * 100) / 100.0

def get_settings(chat_id):
    cursor.execute('SELECT rate, fee_rate, commission_rate FROM settings WHERE chat_id=?', (chat_id,))
    row = cursor.fetchone()
    return row if row else (0, 0, 0)

def show_summary(chat_id):
    cursor.execute('SELECT * FROM transactions WHERE chat_id=?', (chat_id,))
    records = cursor.fetchall()
    total_cny = sum(row[2] for row in records if row[10] in ('未下发', '已下发'))
    total_usdt = sum(row[9] for row in records if row[10] != '删除')
    total_sent = sum(row[9] for row in records if row[10] == '已下发')
    total_commission = sum(row[8] for row in records if row[10] != '删除')
    count_total = len([r for r in records if r[10] != '删除'])
    count_sent = len([r for r in records if r[10] == '已下发'])

    rate, fee, commission = get_settings(chat_id)
    reply = f"已入款（{count_total}笔）：{total_cny} 元\n"
    reply += f"已下发（{count_sent}笔）：{round(total_sent, 2)} (USDT)\n"
    reply += f"总入款金额：{total_cny}\n"
    reply += f"固定汇率：{rate}\n固定费率：{fee}%"
    if commission > 0:
        reply += f"\n中介佣金：{commission}%"
    reply += f"\n\n应下发：{ceil2(total_usdt)} (USDT)"
    reply += f"\n已下发：{ceil2(total_sent)} (USDT)"
    reply += f"\n未下发：{ceil2(total_usdt - total_sent)} (USDT)"
    if commission > 0:
        reply += f"\n\n中介佣金应下发：{ceil2(total_commission)} (USDT)"
    return reply

@bot.message_handler(commands=['start'])
def welcome(message):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add('📌 设置交易汇率', '📘 指令大全')
    if message.chat.type == 'private':
        markup.add('🧹 计算重启')
    bot.send_message(message.chat.id, "欢迎使用记账机器人，请选择：", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == '📘 指令大全')
def show_commands(message):
    bot.send_message(message.chat.id, "📘 操作指令大全：\n\n"
                                     "➕ 加单：+1000 或 加1000\n"
                                     "➖ 删单：-1000 或 减1000（删除最近一笔）\n"
                                     "📤 下发：下发500（将一笔未下发的记录设为已下发）\n"
                                     "⚙️ 设置：使用 '设置\n固定汇率:x\n固定费率:x%\n中介佣金:x%' 设定参数\n")

@bot.message_handler(func=lambda m: m.text == '📌 设置交易汇率')
def prompt_set(message):
    bot.send_message(message.chat.id, "设置\n固定汇率：\n固定费率：\n中介佣金：")

@bot.message_handler(func=lambda m: m.text == '🧹 计算重启')
def clear_chat_data(message):
    chat_id = message.chat.id
    cursor.execute('DELETE FROM transactions WHERE chat_id=?', (chat_id,))
    cursor.execute('DELETE FROM settings WHERE chat_id=?', (chat_id,))
    conn.commit()
    bot.send_message(chat_id, "✅ 当前窗口数据已归零，可重新开始设置")

@bot.message_handler(func=lambda m: m.text.startswith('设置'))
def set_rates(message):
    chat_id = message.chat.id
    lines = message.text.strip().split('\n')
    rate = fee = commission = None
    for line in lines:
        clean = line.replace('：', ':').replace('%', '').replace(' ', '')
        if '固定汇率' in clean or '汇率' in clean:
            rate = float(re.search(r'(\d+\.?\d*)', clean).group(1))
        elif '固定费率' in clean or '费率' in clean:
            fee = float(re.search(r'(\d+\.?\d*)', clean).group(1))
        elif '佣金' in clean:
            commission = float(re.search(r'(\d+\.?\d*)', clean).group(1))

    if rate is None or fee is None or commission is None:
        vals = re.findall(r'(\d+\.?\d*)', message.text.replace('%', ''))
        if len(vals) == 3:
            rate, fee, commission = map(float, vals)

    if rate is not None:
        cursor.execute('REPLACE INTO settings(chat_id, rate, fee_rate, commission_rate) VALUES (?, ?, ?, ?)',
                       (chat_id, rate, fee, commission))
        conn.commit()
        bot.reply_to(message, f"设置成功\n固定汇率：{rate}\n固定费率：{fee}%\n中介佣金：{commission}%")

@bot.message_handler(func=lambda m: re.match(r'^(\+|加)\s*-?\d+', m.text.replace(' ', '')))
def add_entry(message):
    chat_id = message.chat.id
    amount = float(re.sub(r'[^\d\.-]', '', message.text))
    rate, fee, commission = get_settings(chat_id)
    if rate == 0:
        bot.reply_to(message, "请先设置汇率")
        return

    usdt = ceil2(amount / rate)
    fee_u = ceil2(usdt * fee / 100)
    comm_u = ceil2(usdt * commission / 100)
    final = ceil2(usdt - fee_u - comm_u)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''INSERT INTO transactions(chat_id, amount_cny, rate, fee_rate, commission_rate,
                      amount_usdt, fee_usdt, commission_usdt, final_usdt, status, date)
                      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                   (chat_id, amount, rate, fee, commission, usdt, fee_u, comm_u, final, '未下发', now))
    conn.commit()
    bot.reply_to(message, f"✅ 已入款 +{amount} 元\n\n" + show_summary(chat_id))

@bot.message_handler(func=lambda m: re.match(r'^[-减]\s*\d+', m.text.strip()))
def remove_last(message):
    chat_id = message.chat.id
    try:
        amt = float(re.sub(r'[^\d\.-]', '', message.text))
        cursor.execute('SELECT id FROM transactions WHERE chat_id=? AND status="未下发" ORDER BY id DESC LIMIT 1', (chat_id,))
        row = cursor.fetchone()
        if row:
            cursor.execute('UPDATE transactions SET status="删除" WHERE id=?', (row[0],))
            conn.commit()
            bot.reply_to(message, f"已删除最近一笔 {amt} 元\n\n" + show_summary(chat_id))
        else:
            bot.reply_to(message, "❌ 无记录可删除")
    except:
        bot.reply_to(message, "❌ 删除失败")

@bot.message_handler(func=lambda m: re.match(r'^下发\s*-?\d+', m.text.replace(' ', '')))
def send_fund(message):
    chat_id = message.chat.id
    amt = float(re.sub(r'[^\d\.-]', '', message.text))
    cursor.execute('SELECT id FROM transactions WHERE chat_id=? AND status="未下发" ORDER BY id ASC LIMIT 1', (chat_id,))
    row = cursor.fetchone()
    if row:
        cursor.execute('UPDATE transactions SET status="已下发" WHERE id=?', (row[0],))
        conn.commit()
        bot.reply_to(message, f"✅ 下发 {amt} 元\n\n" + show_summary(chat_id))
    else:
        bot.reply_to(message, "❌ 无可下发记录")

@bot.message_handler(commands=['resetme'])
def reset_user_data(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if user_id != 6245295429:
        bot.reply_to(message, "❌ 你没有权限执行此操作")
        return
    cursor.execute('DELETE FROM transactions WHERE chat_id=?', (chat_id,))
    cursor.execute('DELETE FROM settings WHERE chat_id=?', (chat_id,))
    conn.commit()
    bot.reply_to(message, "✅ 数据已归零，你可以重新开始设置。")

if __name__ == '__main__':
    keep_alive()
    print("🤖 Bot 已启动...")
    bot.polling(none_stop=True)
