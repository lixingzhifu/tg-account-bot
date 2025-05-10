import os
import telebot
from telebot import types
import handlers as H

TOKEN = os.getenv("TOKEN")
bot   = telebot.TeleBot(TOKEN)

bot.message_handler(commands=["start"])(H.handle_start)
bot.message_handler(commands=["reset"])(H.handle_reset)
bot.message_handler(commands=["trade"])(H.handle_trade_cmd)
bot.message_handler(func=lambda m: m.text in ("💱 设置交易","设置交易"))(H.handle_trade_cmd)
bot.message_handler(func=lambda m: "设置交易指令" in (m.text or ""))(H.handle_set_config)

bot.message_handler(func=lambda m: bool(m.text and (m.text.strip().startswith("+") or m.text.strip().startswith("加"))))(H.handle_amount)
bot.message_handler(func=lambda m: m.text and m.text.strip().startswith("-"))(H.handle_delete_latest)
bot.message_handler(func=lambda m: m.text and m.text.startswith("删除订单"))(H.handle_delete_specific)

bot.message_handler(commands=["summary"])(H.handle_summary)
bot.message_handler(func=lambda m: m.text=="📊 汇总")(H.handle_summary)

bot.remove_webhook()
bot.infinity_polling()
