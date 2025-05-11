import os
import re
import math
from datetime import datetime, timedelta

import telebot
import psycopg2
from psycopg2.extras import RealDictCursor

# ——— 配置 ———
TOKEN        = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
bot = telebot.TeleBot(TOKEN)

# ——— 数据库连接 & 建表 ———
conn   = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id BIGINT,
  user_id BIGINT,
  currency TEXT DEFAULT 'RMB',
  rate DOUBLE PRECISION DEFAULT 0,
  fee_rate DOUBLE PRECISION DEFAULT 0,
  commission_rate DOUBLE PRECISION DEFAULT 0,
  PRIMARY KEY(chat_id, user_id)
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
  date TIMESTAMP DEFAULT NOW()
);
""")
conn.commit()

# ——— 工具函数 ———
def now_ml():
    """马来西亚时间 = UTC +8"""
    return datetime.utcnow() + timedelta(hours=8)

def ceil2(x):
    return math.ceil(x * 100) / 100.0

def get_settings(chat_id, user_id):
    cursor.execute(
        "SELECT currency, rate, fee_rate, commission_rate "
        "FROM settings WHERE chat_id=%s AND user_id=%s",
        (chat_id, user_id)
    )
    row = cursor.fetchone()
    if row:
        return row["currency"], row["rate"], row["fee_rate"], row["commission_rate"]
    return "RMB", 0.0, 0.0, 0.0

def show_summary(chat_id, user_id):
    cursor.execute(
        "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY id",
        (chat_id, user_id)
    )
    recs = cursor.fetchall()
    total = sum(r["amount"] for r in recs)
    currency, rate, fee, commission = get_settings(chat_id, user_id)
    after = ceil2(total * (1 - fee/100))
    usdt = ceil2(after / rate) if rate else 0
    com_rmb = ceil2(total * commission/100)
    com_usdt = ceil2(com_rmb / rate) if rate else 0

    lines = []
    for r in recs:
        t = (r["date"] + timedelta(hours=8)).strftime("%H:%M:%S")
        after_fee = r["amount"] * (1 - r["fee_rate"]/100)
        usd = ceil2(after_fee / r["rate"]) if r["rate"] else 0
        lines.append(f"{r['id']:03d}. {t} {r['amount']}*{1-r['fee_rate']/100:.2f}/{r['rate']} = {usd}  {r['name']}")
        if r["commission_rate"] > 0:
            cm = ceil2(r["amount"] * r["commission_rate"]/100)
            lines.append(f"{r['id']:03d}. {t} {r['amount']}*{r['commission_rate']/100:.3f} = {cm} 【佣金】")

    summary = "\n".join(lines) + "\n\n"
    summary += (
        f"已入款（{len(recs)}笔）：{total} ({currency})\n"
        f"总入款金额：{total} ({currency})\n"
        f"汇率：{rate}\n费率：{fee}%\n佣金：{commission}%\n\n"
        f"应下发：{after}({currency}) | {usdt}(USDT)\n"
        f"已下发：0.0({currency}) | 0.0(USDT)\n"
        f"未下发：{after}({currency}) | {usdt}(USDT)\n"
    )
    if commission > 0:
        summary += f"\n中介佣金应下发：{com_rmb}({currency}) | {com_usdt}(USDT)"
    return summary

# ——— /start & 菜单 ———
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text.strip() == '记账')
def cmd_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('📊 汇总', '🗑️ 删除订单')
    bot.reply_to(m, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

# ——— 查看 chat_id/user_id ———
@bot.message_handler(commands=['id'])
def cmd_id(m):
    bot.reply_to(m, f"chat_id: `{m.chat.id}`\nuser_id: `{m.from_user.id}`", parse_mode='Markdown')

# ——— /trade & 显示模板 ———
@bot.message_handler(commands=['trade'])
@bot.message_handler(func=lambda m: m.text.strip() in ['设置交易','💱 设置交易'])
def cmd_trade(m):
    bot.reply_to(m,
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )

# ——— 解析“设置交易指令” ———
@bot.message_handler(func=lambda m: m.text.startswith('设置交易指令'))
def cmd_set_trade(m):
    chat, user = m.chat.id, m.from_user.id
    text = m.text.replace('：',':').splitlines()
    cur = rate = fee = comm = None
    for L in text:
        if L.startswith('设置货币:'):    cur = L.split(':',1)[1].strip().upper()
        if L.startswith('设置汇率:'):    rate = float(re.findall(r'\d+\.?\d*',L)[0])
        if L.startswith('设置费率:'):    fee  = float(re.findall(r'\d+\.?\d*',L)[0])
        if L.startswith('中介佣金:'): comm = float(re.findall(r'\d+\.?\d*',L)[0])
    if rate is None:
        return bot.reply_to(m, "❌ 请至少填写“设置汇率：9”")
    cursor.execute("""
        INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
        VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(chat_id,user_id) DO UPDATE SET
          currency=EXCLUDED.currency,
          rate=EXCLUDED.rate,
          fee_rate=EXCLUDED.fee_rate,
          commission_rate=EXCLUDED.commission_rate
    """, (chat,user,cur or 'RMB', rate, fee or 0, comm or 0))
    conn.commit()
    bot.reply_to(m,
        f"✅ 设置成功\n"
        f"货币：{cur or 'RMB'}\n"
        f"汇率：{rate}\n"
        f"费率：{fee or 0}%\n"
        f"中介佣金：{comm or 0}%"
    )

# ——— 入笔 +1000 ———
@bot.message_handler(func=lambda m: re.match(r'^[+]\s*\d+(\.\d+)?$', m.text or ''))
def cmd_add(m):
    chat,user = m.chat.id, m.from_user.id
    currency, rate, fee, comm = get_settings(chat,user)
    if rate == 0:
        return bot.reply_to(m, "⚠️ 请先设置交易后再入笔")
    amt = float(re.findall(r'\d+\.?\d*', m.text)[0])
    name = m.from_user.username or m.from_user.first_name or '匿名'
    now = now_ml()
    cursor.execute("""
        INSERT INTO transactions(chat_id,user_id,name,amount,rate,fee_rate,commission_rate,currency,date)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (chat,user,name,amt,rate,fee,comm,currency,now))
    conn.commit()
    bot.reply_to(m,
        f"✅ 已入款 +{amt}\n编号：{cursor.lastrowid}\n"
        + show_summary(chat,user)
    )

# ——— 删除最近一笔 ———
@bot.message_handler(func=lambda m: m.text.strip() == '-')
def cmd_del_last(m):
    chat,user = m.chat.id, m.from_user.id
    cursor.execute("""
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s
        ORDER BY id DESC LIMIT 1
    """,(chat,user))
    conn.commit()
    bot.reply_to(m,"✅ 已删除最近一笔")

# ——— 按编号删除 ———
@bot.message_handler(func=lambda m: m.text.startswith('删除订单'))
def cmd_del_id(m):
    chat,user = m.chat.id, m.from_user.id
    parts = m.text.split()
    if len(parts)!=2 or not parts[1].isdigit():
        return bot.reply_to(m,"❌ 格式：删除订单 001")
    tid = int(parts[1])
    cursor.execute("""
        DELETE FROM transactions
        WHERE chat_id=%s AND user_id=%s AND id=%s
    """,(chat,user,tid))
    if cursor.rowcount:
        conn.commit()
        bot.reply_to(m,f"✅ 删除订单成功，编号：{tid:03d}")
    else:
        bot.reply_to(m,"⚠️ 未找到该编号")

# ——— 汇总 ———
@bot.message_handler(func=lambda m: m.text.strip() in ['汇总','/summary','📊 汇总'])
def cmd_sum(m):
    bot.reply_to(m, show_summary(m.chat.id, m.from_user.id))

# ——— 启动 ———
if __name__ == "__main__":
    bot.remove_webhook()
    bot.infinity_polling()
