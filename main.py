# main.py
import os
from telebot import TeleBot, types

TOKEN = os.getenv("TOKEN")
bot = TeleBot(TOKEN)

# 1. /start 和 “记账” 都触发欢迎菜单
@bot.message_handler(commands=['start'])
def handle_start(msg):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row('💱 设置交易', '📘 指令大全')
    bot.send_message(
        msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择菜单指令：",
        reply_markup=kb
    )

@bot.message_handler(func=lambda m: m.text == '记账')
def handle_start_alias(msg):
    handle_start(msg)

# 2. 点击 “设置交易” 或输入 /trade
@bot.message_handler(commands=['trade'])
@bot.message_handler(func=lambda m: m.text in ['设置交易', '💱 设置交易'])
def handle_trade_cmd(msg):
    # 如果是在群里，必须是管理员或群主才能继续
    if msg.chat.type != 'private':
        member = bot.get_chat_member(msg.chat.id, msg.from_user.id)
        if member.status not in ['administrator', 'creator']:
            bot.reply_to(msg, "❌ 只有群管理员才能设置交易参数")
            return

    # 私聊或管理员，展示模板
    template = (
        "请按以下格式发送：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：0\n"
        "设置费率：0\n"
        "中介佣金：0"
    )
    bot.reply_to(msg, template)

# 3. 引入剩余 handler（入笔／汇总 等），等我们下一步再补
import handlers

if __name__ == '__main__':
    bot.remove_webhook()
    bot.infinity_polling()
