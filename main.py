# main.py
import os, re
from telebot import TeleBot, types
from db import conn, cursor

TOKEN = os.getenv("TOKEN")
bot = TeleBot(TOKEN)

@bot.message_handler(commands=['start','记账'])
def cmd_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('设置交易')
    bot.reply_to(msg,
                 "欢迎使用 LX 记账机器人 ✅\n请选择：",
                 reply_markup=kb)

@bot.message_handler(func=lambda m: m.text=='设置交易' or m.text.startswith('/trade'))
def cmd_set_trade(msg):
    lines = msg.text.strip().splitlines()
    if len(lines)!=5 or not lines[0].startswith('设置交易指令'):
        return bot.reply_to(msg,
            "请按以下格式发送：\n"
            "设置交易指令\n"
            "设置货币：RMB\n"
            "设置汇率：9\n"
            "设置费率：2\n"
            "中介佣金：0.5"
        )
    try:
        currency   = lines[1].split('：',1)[1]
        rate       = float(lines[2].split('：',1)[1])
        fee        = float(lines[3].split('：',1)[1])
        commission = float(lines[4].split('：',1)[1])
    except:
        return bot.reply_to(msg, "设置错误，请检查数字格式")

    chat_id = msg.chat.id
    user_id = msg.from_user.id
    try:
        cursor.execute("""
            INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)
            VALUES(%s,%s,%s,%s,%s,%s)
            ON CONFLICT(chat_id,user_id) DO UPDATE SET
              currency=EXCLUDED.currency,
              rate=EXCLUDED.rate,
              fee_rate=EXCLUDED.fee_rate,
              commission_rate=EXCLUDED.commission_rate
        """, (chat_id,user_id,currency,rate,fee,commission))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    return bot.reply_to(msg,
        f"✅ 设置成功\n"
        f"货币：{currency}\n"
        f"汇率：{rate}\n"
        f"费率：{fee}%\n"
        f"中介佣金：{commission}%"
    )

if __name__=="__main__":
    bot.remove_webhook()
    bot.infinity_polling()
