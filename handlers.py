# handlers.py

import re
from datetime import timedelta
from telebot import types
from main import bot                   # 从你的主模块导入 bot 实例
from db import (                        # 数据库操作都在 db.py 中
    get_settings,
    upsert_settings,
    add_transaction,
    delete_latest,
    delete_by_id,
    fetch_all
)
from utils import (                     # 工具函数在 utils.py 中
    parse_trade_text,
    human_now,
    ceil2,
    parse_amount_text
)

def is_admin(bot, chat_id, user_id):
    """检查用户是否有权限（私聊永远有权限，群聊需为管理员）。"""
    ct = bot.get_chat(chat_id).type
    if ct in ("group", "supergroup"):
        admins = bot.get_chat_administrators(chat_id)
        return any(ad.user.id == user_id for ad in admins)
    return True

# ——————————————————————————————
# /start 或 记账
@bot.message_handler(commands=['start'])
@bot.message_handler(func=lambda m: m.text == '记账')
def handle_start(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💱 设置交易", "/trade")
    markup.row("🔁 清空记录", "/reset")
    markup.row("📊 汇总", "/summary")
    bot.send_message(
        message.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=markup
    )

# ——————————————————————————————
# /reset 清空
@bot.message_handler(commands=['reset'])
def handle_reset(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ 无权限")
    # 直接删掉这用户这聊天的所有 transactions
    from db import conn, cursor
    cursor.execute(
        "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
        (message.chat.id, message.from_user.id)
    )
    conn.commit()
    bot.reply_to(message, "🔄 已清空本群组本用户的所有记录")

# ——————————————————————————————
# /trade 或 点击【设置交易】
@bot.message_handler(commands=['trade'])
@bot.message_handler(func=lambda m: m.text == '💱 设置交易')
def handle_trade_cmd(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ 无权限")
    bot.reply_to(
        message,
        "格式如下（复制整段并修改数字/货币字母）：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：9\n"
        "设置费率：2\n"
        "中介佣金：0.5"
    )

# ——————————————————————————————
# 解析“设置交易指令…”
@bot.message_handler(func=lambda m: m.text.startswith("设置交易指令"))
def handle_set_config(message):
    if not is_admin(bot, message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ 无权限")
    cur, rate, fee, com, errs = parse_trade_text(message.text)
    if errs:
        return bot.reply_to(message, "设置错误\n" + "\n".join(errs))
    if rate is None:
        return bot.reply_to(message, "❌ 至少需要提供汇率：设置汇率：9")
    upsert_settings(
        message.chat.id,
        message.from_user.id,
        cur or "RMB",
        rate,
        fee or 0,
        com or 0
    )
    bot.reply_to(
        message,
        "✅ 设置成功\n"
        f"设置货币：{cur or 'RMB'}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee or 0}%\n"
        f"设置佣金：{com or 0}%"
    )

# ——————————————————————————————
# 支持 +1000 的英文入款
@bot.message_handler(func=lambda m: re.match(r'^[+]\s*\d+', m.text or ''))
# 支持 “入1000” 或 “入笔1000” 的中文入款
@bot.message_handler(func=lambda m: re.match(r'^(入笔|入)\s*\d+', m.text or ''))
def handle_amount(message):
    # DEBUG 日志，部署后可删
    print(f"【DEBUG】handle_amount 收到：{message.text}")

    # 权限 & 配置检查
    if message.chat.type in ("group", "supergroup") and not is_admin(bot, message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ 无权限")

    cfg = get_settings(message.chat.id, message.from_user.id)
    if not cfg:
        return bot.reply_to(message, "❌ 请先“设置交易”并填写汇率，才能入笔。")

    name, amount = parse_amount_text(message.text)
    if amount is None:
        return  # 如果正则没取到数字，就不做任何回复

    now_hms, now_dt = human_now()
    new_id = add_transaction(
        message.chat.id,
        message.from_user.id,
        name or message.from_user.first_name or "匿名",
        amount,
        cfg[1],  # rate
        cfg[2],  # fee_rate
        cfg[3],  # commission_rate
        cfg[0],  # currency
        now_dt,
        message.message_id
    )

    # 计算
    aft = amount * (1 - cfg[2] / 100)
    usdt = ceil2(aft / cfg[1]) if cfg[1] else 0
    com_rmb = ceil2(amount * (cfg[3] / 100))
    com_usdt = ceil2(com_rmb / cfg[1]) if cfg[1] else 0

    # 构建回复
    s  = f"✅ 已入款 +{amount:.1f}\n"
    s += f"编号：{new_id:03d}\n"
    s += f"1. {now_hms} {amount:.1f}*{1-cfg[2]/100:.2f}/{cfg[1]:.1f} = {usdt:.2f}  {name}\n"
    if cfg[3] > 0:
        s += f"1. {now_hms} {amount:.1f}*{cfg[3]/100:.3f} = {com_rmb:.2f} 【佣金】\n"

    bot.reply_to(message, s)

# ——————————————————————————————
# 删除最近一笔
@bot.message_handler(func=lambda m: re.match(r'^-\s*\d+', m.text or ''))
def handle_delete_latest(message):
    if message.chat.type in ("group", "supergroup") and not is_admin(bot, message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ 无权限")
    tid = delete_latest(message.chat.id, message.from_user.id)
    if not tid:
        return bot.reply_to(message, "⚠️ 无可删除记录")
    bot.reply_to(message, f"✅ 删除订单成功，编号：{tid:03d}")

# ——————————————————————————————
# 删除指定编号
@bot.message_handler(func=lambda m: re.match(r'^删除订单\s*\d+', m.text or ''))
def handle_delete_specific(message):
    if message.chat.type in ("group", "supergroup") and not is_admin(bot, message.chat.id, message.from_user.id):
        return bot.reply_to(message, "❌ 无权限")
    num = int(re.findall(r"\d+", message.text)[0])
    tid = delete_by_id(message.chat.id, message.from_user.id, num)
    if not tid:
        return bot.reply_to(message, f"⚠️ 找不到编号：{num:03d}")
    bot.reply_to(message, f"✅ 删除订单成功，编号：{num:03d}")

# ——————————————————————————————
# /summary 或 点击【汇总】
@bot.message_handler(commands=['summary'])
@bot.message_handler(func=lambda m: m.text == '📊 汇总')
def handle_summary(message):
    cfg = get_settings(message.chat.id, message.from_user.id)
    if not cfg:
        return bot.reply_to(message, "❌ 请先“设置交易”并填写汇率，才能查看汇总。")

    rows = fetch_all(message.chat.id, message.from_user.id)
    total = sum(r["amount"] for r in rows)
    usdt = ceil2(total * (1 - cfg[2] / 100) / cfg[1]) if cfg[1] else 0
    com_rmb = ceil2(total * (cfg[3] / 100))
    com_usdt = ceil2(com_rmb / cfg[1]) if cfg[1] else 0

    lines = []
    for i, r in enumerate(rows, 1):
        t = (r["created_at"] + timedelta(hours=8)).strftime("%H:%M:%S")
        aft = r["amount"] * (1 - r["fee_rate"] / 100)
        u = ceil2(aft / r["rate"]) if r["rate"] else 0
        lines.append(f"{i}. {t} {r['amount']:.1f}*{1-r['fee_rate']/100:.2f}/{r['rate']:.1f} = {u:.2f} {r['name']}")
        if r["commission_rate"] > 0:
            cm = ceil2(r['amount'] * r['commission_rate'] / 100)
            lines.append(f"{i}. {t} {r['amount']:.1f}*{r['commission_rate']/100:.3f} = {cm:.2f} 【佣金】")

    summary = "\n".join(lines)
    summary += (
        f"\n\n已入款（{len(rows)}笔）：{total:.1f} (RMB)\n"
        f"应下发：{ceil2(total*(1-cfg[2]/100)):.1f}(RMB) | {usdt:.2f}(USDT)\n"
        f"中介佣金：{com_rmb:.2f}(RMB) | {com_usdt:.2f}(USDT)"
    )
    bot.reply_to(message, summary)
