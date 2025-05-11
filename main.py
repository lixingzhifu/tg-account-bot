# main.py

import os
import re
import math
from datetime import datetime, timedelta

import telebot
from psycopg2.extras import RealDictCursor
from db import conn, cursor
from utils import ceil2, now_ml, get_settings, show_summary

TOKEN = os.getenv("TOKEN")
bot   = telebot.TeleBot(TOKEN)

# ——— 菜单 & 设置部分 ———
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text.strip() == '记账')
def cmd_start(m):
    kb = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    kb.row('📊 汇总', '🗑️ 删除订单')
    bot.reply_to(m, "欢迎使用 LX 记账机器人 ✅\n请选择：", reply_markup=kb)

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

@bot.message_handler(func=lambda m: m.text.startswith('设置交易指令'))
def cmd_set_trade(m):
    chat, user = m.chat.id, m.from_user.id
    text = m.text.replace('：',':').splitlines()
    cur = rate = fee = comm = None
    for L in text:
        if L.startswith('设置货币:'):    cur = L.split(':',1)[1].strip().upper()
        if L.startswith('设置汇率:'):    rate = float(re.findall(r'\d+\.?\d*', L)[0])
        if L.startswith('设置费率:'):    fee  = float(re.findall(r'\d+\.?\d*', L)[0])
        if L.startswith('中介佣金:'): comm = float(re.findall(r'\d+\.?\d*', L)[0])
    if rate is None:
        return bot.reply_to(m, "❌ 请至少填写“设置汇率：9”")
    cursor.execute("""
      INSERT INTO settings(chat_id, user_id, currency, rate, fee_rate, commission_rate)
      VALUES(%s,%s,%s,%s,%s,%s)
      ON CONFLICT(chat_id,user_id) DO UPDATE SET
        currency=EXCLUDED.currency,
        rate=EXCLUDED.rate,
        fee_rate=EXCLUDED.fee_rate,
        commission_rate=EXCLUDED.commission_rate
    """, (chat, user, cur or 'RMB', rate, fee or 0, comm or 0))
    conn.commit()
    bot.reply_to(m,
      f"✅ 设置成功\n"
      f"货币：{cur or 'RMB'}\n"
      f"汇率：{rate}\n"
      f"费率：{fee or 0}%\n"
      f"中介佣金：{comm or 0}%"
    )

import transactions   # ← 加载下面的入笔/删除逻辑

if __name__ == "__main__":
    bot.remove_webhook()
    bot.infinity_polling()
