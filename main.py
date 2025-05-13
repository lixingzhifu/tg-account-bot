import telebot
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from telebot import types
import re
from datetime import datetime
import urllib.parse

# 环境变量
DATABASE_URL = os.getenv('DATABASE_URL')
TOKEN = os.getenv('TOKEN')

# 数据库连接
parsed_url = urllib.parse.urlparse(DATABASE_URL)
conn = psycopg2.connect(
    database=parsed_url.path[1:],  # Remove leading slash
    user=parsed_url.username,
    password=parsed_url.password,
    host=parsed_url.hostname,
    port=parsed_url.port
)
cursor = conn.cursor(cursor_factory=RealDictCursor)

# 设置机器人
bot = telebot.TeleBot(TOKEN)

# 初始化数据库
def init_db():
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT,
            user_id BIGINT,
            name VARCHAR(255),
            amount FLOAT NOT NULL,
            rate FLOAT,
            fee_rate FLOAT,
            commission_rate FLOAT,
            currency VARCHAR(10),
            date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_id BIGINT
        );
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT,
            user_id BIGINT,
            exchange_rate FLOAT,
            fee_rate FLOAT,
            commission_rate FLOAT,
            currency VARCHAR(10)
        );
    ''')
    conn.commit()

# /start 触发
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "欢迎使用 LX 记账机器人 ✅\n请选择菜单选项：")
    # 构建菜单
    markup = types.ReplyKeyboardMarkup(row_width=2)
    markup.add(types.KeyboardButton("设置交易"), types.KeyboardButton("显示账单"))
    markup.add(types.KeyboardButton("指令大全"), types.KeyboardButton("客服帮助"))
    markup.add(types.KeyboardButton("计算重启"), types.KeyboardButton("定制机器人"))
    bot.send_message(message.chat.id, "请选择：", reply_markup=markup)

# 设置交易（按钮触发）
@bot.message_handler(func=lambda message: message.text == "设置交易")
def ask_for_settings(message):
    bot.send_message(message.chat.id, "请按下面格式发送：\n\n"
                                      "设置交易指令\n设置汇率：0\n设置费率：0\n中介佣金：0.0")
    bot.register_next_step_handler(message, save_settings)

# 保存设置
def save_settings(message):
    try:
        # 获取用户输入的设置值
        settings = message.text.split("\n")
        
        if len(settings) != 4:  # 确保包含4行
            bot.send_message(message.chat.id, "格式错误，请按照以下格式重新输入：\n\n"
                                              "设置交易指令\n设置汇率：0\n设置费率：0\n中介佣金：0.0")
            bot.register_next_step_handler(message, save_settings)
            return

        # 解析输入内容
        exchange_rate = float(settings[1].split("：")[1].strip())  # 去除空格和特殊字符
        fee_rate = float(settings[2].split("：")[1].strip())
        commission_rate = float(settings[3].split("：")[1].strip())

        # 存储到数据库
        cursor.execute("INSERT INTO settings (chat_id, user_id, exchange_rate, fee_rate, commission_rate, currency) VALUES (%s, %s, %s, %s, %s, %s)",
                       (message.chat.id, message.from_user.id, exchange_rate, fee_rate, commission_rate, "RMB"))
        conn.commit()

        bot.send_message(message.chat.id, f"✅ 设置成功\n设置汇率：{exchange_rate}\n设置费率：{fee_rate}\n中介佣金：{commission_rate}")
    except Exception as e:
        bot.send_message(message.chat.id, "输入有误，请重新设置！")
        bot.register_next_step_handler(message, save_settings)

# /入笔 触发
@bot.message_handler(func=lambda message: re.match(r'^\+?\d+(\.\d+)?$', message.text))
def record_transaction(message):
    try:
        # 解析金额
        amount = float(message.text)

        # 获取设置
        cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s ORDER BY id DESC LIMIT 1", (message.chat.id, message.from_user.id))
        settings = cursor.fetchone()

        if not settings:
            bot.send_message(message.chat.id, "请先设置交易参数。")
            return

        rate = settings['exchange_rate']
        fee_rate = settings['fee_rate']
        commission_rate = settings['commission_rate']

        # 计算应下发金额和佣金
        deducted_amount = amount * (1 - fee_rate / 100)
        commission = deducted_amount * commission_rate / 100
        final_amount = deducted_amount - commission

        # 插入交易记录
        cursor.execute("INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency, message_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                       (message.chat.id, message.from_user.id, message.from_user.username, amount, rate, fee_rate, commission_rate, "RMB", message.message_id))
        conn.commit()

        bot.send_message(message.chat.id, f"今日入笔（1笔）\n✅ 已入款 +{amount} (RMB)\n编号：{message.message_id}\n"
                                         f"汇率：{rate}\n费率：{fee_rate}%\n佣金：{commission_rate}%\n应下发：{final_amount} (USDT)\n")
    except Exception as e:
        bot.send_message(message.chat.id, "交易失败，请重试。")

# /删除入款 触发
@bot.message_handler(func=lambda message: message.text.startswith("删除"))
def delete_transaction(message):
    try:
        transaction_id = int(message.text.split("删除")[1].strip())

        cursor.execute("DELETE FROM transactions WHERE message_id = %s", (transaction_id,))
        conn.commit()

        bot.send_message(message.chat.id, f"已删除编号 {transaction_id} 的交易记录。")
    except Exception as e:
        bot.send_message(message.chat.id, "无法删除记录，请检查编号或重试。")

# /显示账单 触发
@bot.message_handler(func=lambda message: message.text == "显示账单")
def show_bill(message):
    cursor.execute("SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date DESC", (message.chat.id, message.from_user.id))
    transactions = cursor.fetchall()

    if not transactions:
        bot.send_message(message.chat.id, "今天没有交易记录。")
        return

    response = "今日账单：\n"
    for transaction in transactions:
        response += f"编号：{transaction['message_id']} | 金额：{transaction['amount']} | 日期：{transaction['date']} \n"

    bot.send_message(message.chat.id, response)

# /指令大全 触发
@bot.message_handler(func=lambda message: message.text == "指令大全")
def show_commands(message):
    commands = """
    /start - 启动机器人
    设置交易指令 - 设置汇率、费率、佣金
    /入笔 + 数字 - 记录交易
    删除 + 数字 - 删除指定编号的交易
    /显示账单 - 查看今日账单
    /指令大全 - 查看所有指令
    /客服帮助 - 获取帮助
    /计算重启 - 重置所有数据
    """
    bot.send_message(message.chat.id, commands)

# /reset 触发
@bot.message_handler(func=lambda message: message.text == "/reset")
def reset_data(message):
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s", (message.chat.id, message.from_user.id))
    cursor.execute("DELETE FROM settings WHERE chat_id=%s AND user_id=%s", (message.chat.id, message.from_user.id))
    conn.commit()
    bot.send_message(message.chat.id, "已重置您的所有数据。")

# 启动机器人
if __name__ == '__main__':
    init_db()  # 初始化数据库
    bot.polling(none_stop=True)
