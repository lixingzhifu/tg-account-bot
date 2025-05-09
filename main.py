from aiogram import Bot, Dispatcher, types, executor
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
import asyncio
import os
from datetime import datetime
from config import BOT_TOKEN
from db import init_db, get_settings, update_setting, add_record, get_records, reset_user_data

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

# --- Keyboards ---
menu_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
menu_keyboard.add(
    KeyboardButton("菜单")
)

submenu_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
submenu_keyboard.add(
    KeyboardButton("设置交易"),
    KeyboardButton("指令大全")
)
submenu_keyboard.add(
    KeyboardButton("计算重启"),
    KeyboardButton("需要帮助"),
    KeyboardButton("定制机器人")
)

@dp.message_handler(commands=['start'])
async def send_welcome(message: types.Message):
    await message.reply("欢迎使用记账机器人，请点击下方菜单进行操作。", reply_markup=menu_keyboard)

@dp.message_handler(lambda m: m.text == "菜单")
async def show_menu(message: types.Message):
    await message.reply("请选择功能：", reply_markup=submenu_keyboard)

@dp.message_handler(lambda m: m.text == "设置交易")
async def setup_transaction(message: types.Message):
    await message.reply(
        "设置交易指令\n设置货币：\n设置汇率：\n设置费率：\n中介佣金："
    )

@dp.message_handler(lambda m: m.text.startswith("设置汇率："))
async def set_rate(message: types.Message):
    user_id = message.from_user.id
    try:
        rate = float(message.text.split("：")[1])
        await update_setting(user_id, "rate", rate)
        await message.reply(f"✅ 汇率设置为：{rate}")
    except:
        await message.reply("设置失败，请输入格式：设置汇率：9")

@dp.message_handler(lambda m: m.text.startswith("设置费率："))
async def set_fee(message: types.Message):
    user_id = message.from_user.id
    try:
        fee = float(message.text.split("：")[1])
        await update_setting(user_id, "fee", fee)
        await message.reply(f"✅ 费率设置为：{fee}%")
    except:
        await message.reply("设置失败，请输入格式：设置费率：2")

@dp.message_handler(lambda m: m.text.startswith("中介佣金："))
async def set_commission(message: types.Message):
    user_id = message.from_user.id
    try:
        comm = float(message.text.split("：")[1])
        await update_setting(user_id, "commission", comm)
        await message.reply(f"✅ 中介佣金设置为：{comm}%")
    except:
        await message.reply("设置失败，请输入格式：中介佣金：0.5")

@dp.message_handler(lambda m: m.text == "计算重启")
async def reset_user(message: types.Message):
    user_id = message.from_user.id
    await reset_user_data(user_id)
    await message.reply("✅ 所有记录已清零")

@dp.message_handler(lambda m: m.text == "需要帮助")
async def help_link(message: types.Message):
    await message.reply("加入官方群组获取帮助：https://t.me/yourgroup")

@dp.message_handler(lambda m: m.text == "定制机器人")
async def custom_bot_link(message: types.Message):
    await message.reply("联系开发者定制：https://t.me/yourgroup")

@dp.message_handler(lambda m: '+' in m.text)
async def handle_transaction(message: types.Message):
    user_id = message.from_user.id
    text = message.text.strip()
    name = message.from_user.first_name
    try:
        if any(c.isalpha() for c in text):
            for i in range(len(text)):
                if text[i] == '+':
                    name, amt = text[:i], text[i:]
                    break
        else:
            amt = text

        amount = float(amt.strip('+'))
        settings = await get_settings(user_id)

        fee_amt = amount * (1 - settings['fee'] / 100)
        usdt_amt = round(fee_amt / settings['rate'], 2)
        comm_amt = round(amount * settings['commission'] / 100, 2) if settings['commission'] > 0 else 0
        now = datetime.now().strftime("%d-%m-%Y\n%H:%M:%S")

        await add_record(user_id, amount, round(fee_amt, 2), usdt_amt, comm_amt, name, now)
        records = await get_records(user_id)

        response = f"✅ 已入款 +{amount:.1f} ({settings['currency']})\n{now}\n{amount}*{1 - settings['fee']/100:.2f}/{settings['rate']} = {usdt_amt}  {name}"
        if comm_amt:
            response += f"\n{amount}*{settings['commission']/100:.2f} = {comm_amt}"

        total_amount = sum(r['amount'] for r in records)
        total_usdt = sum(r['usdt'] for r in records)
        total_comm = sum(r['comm'] for r in records)

        response += f"\n\n这里是今天的总数\n已入款（{len(records)}笔）：{total_amount:.1f} ({settings['currency']})"
        response += f"\n已下发（0笔）：0 (USDT)"
        response += f"\n\n总入款金额：{total_amount:.1f} ({settings['currency']})\n汇率：{settings['rate']}"
        response += f"\n费率：{settings['fee']}%\n佣金：{settings['commission']}%"
        response += f"\n\n应下发：{fee_amt:.0f}({settings['currency']}) | {usdt_amt} (USDT)"
        response += f"\n已下发：0.0 ({settings['currency']}) | 0.0 (USDT)"
        response += f"\n未下发：{fee_amt:.0f}({settings['currency']}) | {usdt_amt} (USDT)"
        if total_comm:
            response += f"\n\n中介佣金应下发：{round(total_comm,2)} (USDT)"

        await message.reply(response)
    except Exception as e:
        await message.reply("格式错误，例：+1000 或 张飞+1000")

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    executor.start_polling(dp, skip_updates=True)
