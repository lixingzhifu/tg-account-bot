import os, re, pytz
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from telebot import TeleBot, types

TOKEN = os.getenv("TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
bot = TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# —— 建表 —— #
cursor.execute("""
CREATE TABLE IF NOT EXISTS settings (
  chat_id BIGINT, user_id BIGINT,
  rate DOUBLE PRECISION, fee_rate DOUBLE PRECISION, commission_rate DOUBLE PRECISION,
  PRIMARY KEY(chat_id,user_id)
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
  id SERIAL PRIMARY KEY, chat_id BIGINT, user_id BIGINT,
  amount DOUBLE PRECISION, rate DOUBLE PRECISION,
  fee_rate DOUBLE PRECISION, commission_rate DOUBLE PRECISION,
  date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")
conn.commit()

@bot.message_handler(commands=['start'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton('/trade'))
    bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

@bot.message_handler(commands=['trade'])
def cmd_trade(msg):
    bot.reply_to(msg,
        "设置交易指令\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0.0"
    )

@bot.message_handler(func=lambda m: m.text and m.text.startswith("设置交易指令"))
def handle_trade_setting(msg):
    t = msg.text
    try:
        rate = float(re.search(r'设置汇率[:：]\s*([\d.]+)', t).group(1))
        fee  = float(re.search(r'设置费率[:：]\s*([\d.]+)', t).group(1))
        comm = float(re.search(r'中介佣金[:：]\s*([\d.]+)', t).group(1))
    except:
        return bot.reply_to(msg, "❌ 格式错误，请重发。")
    cid, uid = msg.chat.id, msg.from_user.id
    cursor.execute("""
      INSERT INTO settings(chat_id,user_id,rate,fee_rate,commission_rate)
      VALUES(%s,%s,%s,%s,%s)
      ON CONFLICT(chat_id,user_id) DO UPDATE
        SET rate=EXCLUDED.rate, fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate
    """,(cid,uid,rate,fee,comm))
    conn.commit()
    bot.reply_to(msg, f"✅ 设置成功\n汇率：{rate}\n费率：{fee}%\n佣金：{comm}%")

@bot.message_handler(func=lambda m: re.match(r'^[+\d].*', m.text or ''))
def handle_deposit(msg):
    cid, uid = msg.chat.id, msg.from_user.id
    cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s",(cid,uid))
    s = cursor.fetchone()
    if not s:
        return bot.reply_to(msg, "❌ 请先 /trade 设置参数")
    m = re.findall(r'[\+入笔]*([0-9]+(?:\.[0-9]+)?)', msg.text)
    if not m:
        return bot.reply_to(msg, "❌ 格式示例：+1000")
    amount = float(m[0])
    rate, fee, comm = s['rate'], s['fee_rate'], s['commission_rate']
    after_fee = amount*(1-fee/100)
    usdt = round(after_fee/rate,2)
    cursor.execute("""
      INSERT INTO transactions(chat_id,user_id,amount,rate,fee_rate,commission_rate)
      VALUES(%s,%s,%s,%s,%s,%s)
    """,(cid,uid,amount,rate,fee,comm))
    conn.commit()
    bot.reply_to(msg,
        f"✅ 已入款 +{amount}(RMB)\n"
        f"计算：{amount} * (1-{fee/100}) / {rate} = {usdt} USDT"
    )

if __name__=='__main__':
    bot.remove_webhook()
    bot.infinity_polling()
