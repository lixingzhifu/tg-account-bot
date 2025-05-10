import re
from telebot import types
from datetime import timedelta
from db import (
    get_settings, upsert_settings,
    add_transaction, delete_latest, delete_by_id, fetch_all
)
from utils import parse_trade_text, human_now, ceil2, parse_amount_text

def is_admin(bot, chat_id, user_id):
    ct = bot.get_chat(chat_id).type
    if ct in ("group","supergroup"):
        admins = bot.get_chat_administrators(chat_id)
        return any(ad.user.id == user_id for ad in admins)
    return True

def handle_start(bot, msg):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("💱 设置交易", "/trade")
    markup.row("🔁 清空记录", "/reset")
    markup.row("📊 汇总", "/summary")
    bot.send_message(msg.chat.id,
        "欢迎使用 LX 记账机器人 ✅\n请选择：",
        reply_markup=markup
    )

def handle_reset(bot, msg):
    if not is_admin(bot, msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    from db import conn, cursor
    cursor.execute("DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
                   (msg.chat.id, msg.from_user.id))
    conn.commit()
    bot.reply_to(msg, "🔄 已清空本群组本用户的所有记录")

def handle_trade_cmd(bot, msg):
    if not is_admin(bot, msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    bot.reply_to(msg,
        "格式如下（复制整段并修改数字/货币字母）：\n"
        "设置交易指令\n"
        "设置货币：RMB\n"
        "设置汇率：9\n"
        "设置费率：2\n"
        "中介佣金：0.5"
    )

def handle_set_config(bot, msg):
    if not is_admin(bot, msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    cur, rate, fee, com, errs = parse_trade_text(msg.text)
    if errs:
        return bot.reply_to(msg, "设置错误\n" + "\n".join(errs))
    if rate is None:
        return bot.reply_to(msg, "❌ 至少需要提供汇率：设置汇率：9")
    upsert_settings(
        msg.chat.id, msg.from_user.id,
        cur or "RMB", rate, fee or 0, com or 0
    )
    bot.reply_to(msg,
        "✅ 设置成功\n"
        f"设置货币：{cur or 'RMB'}\n"
        f"设置汇率：{rate}\n"
        f"设置费率：{fee or 0}%\n"
        f"设置佣金：{com or 0}%"
    )

def handle_amount(bot, msg):
    # 权限 & 配置检查
    if msg.chat.type in ("group","supergroup") and not is_admin(bot, msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    cfg = get_settings(msg.chat.id, msg.from_user.id)
    if not cfg:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能入笔。")

    name, amount = parse_amount_text(msg.text)
    if amount is None:
        return

    now_hms, now_dt = human_now()
    new_id = add_transaction(
        msg.chat.id, msg.from_user.id,
        name or msg.from_user.first_name or "匿名",
        amount, cfg[1], cfg[2], cfg[3], cfg[0], now_dt, msg.message_id
    )

    aft = amount*(1 - cfg[2]/100)
    usdt = ceil2(aft/cfg[1]) if cfg[1] else 0
    com_rmb = ceil2(amount*(cfg[3]/100))
    com_usdt = ceil2(com_rmb/cfg[1]) if cfg[1] else 0

    s  = f"✅ 已入款 +{amount:.1f} ({cfg[0]})\n"
    s += f"编号：{new_id:03d}\n"
    s += f"1. {now_hms} {amount:.1f}*{1-cfg[2]/100:.2f}/{cfg[1]:.1f} = {usdt:.2f} {name}\n"
    if cfg[3]>0:
        s += f"1. {now_hms} {amount:.1f}*{cfg[3]/100:.3f} = {com_rmb:.2f} 【佣金】\n"
    bot.reply_to(msg, s)

def handle_delete_latest(bot, msg):
    if msg.chat.type in ("group","supergroup") and not is_admin(bot, msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    tid = delete_latest(msg.chat.id, msg.from_user.id)
    if not tid:
        return bot.reply_to(msg, "⚠️ 无可删除记录")
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{tid:03d}")

def handle_delete_specific(bot, msg):
    if msg.chat.type in ("group","supergroup") and not is_admin(bot, msg.chat.id, msg.from_user.id):
        return bot.reply_to(msg, "❌ 无权限")
    num = int(re.findall(r"\d+", msg.text)[0])
    tid = delete_by_id(msg.chat.id, msg.from_user.id, num)
    if not tid:
        return bot.reply_to(msg, f"⚠️ 找不到编号：{num:03d}")
    bot.reply_to(msg, f"✅ 删除订单成功，编号：{num:03d}")

def handle_summary(bot, msg):
    cfg = get_settings(msg.chat.id, msg.from_user.id)
    if not cfg:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能查看汇总。")
    rows = fetch_all(msg.chat.id, msg.from_user.id)
    total = sum(r["amount"] for r in rows)
    usdt = ceil2(total*(1-cfg[2]/100)/cfg[1]) if cfg[1] else 0
    com_rmb = ceil2(total*(cfg[3]/100))
    com_usdt = ceil2(com_rmb/cfg[1]) if cfg[1] else 0

    lines = []
    for i, r in enumerate(rows,1):
        t = (r["created_at"] + timedelta(hours=8)).strftime("%H:%M:%S")
        aft = r["amount"]*(1-r["fee_rate"]/100)
        u = ceil2(aft/r["rate"]) if r["rate"] else 0
        lines.append(f"{i}. {t} {r['amount']:.1f}*{1-r['fee_rate']/100:.2f}/{r['rate']:.1f} = {u:.2f} {r['name']}")
        if r["commission_rate"]>0:
            cm=ceil2(r['amount']*r['commission_rate']/100)
            lines.append(f"{i}. {t} {r['amount']:.1f}*{r['commission_rate']/100:.3f} = {cm:.2f} 【佣金】")

    summary = "\n".join(lines)
    summary += (
        f"\n\n已入款（{len(rows)}笔）：{total:.1f} ({cfg[0]})\n"
        f"应下发：{ceil2(total*(1-cfg[2]/100)):.1f}({cfg[0]}) | {usdt:.2f} (USDT)\n"
        f"中介佣金：{com_rmb:.2f}({cfg[0]}) | {com_usdt:.2f} (USDT)"
    )
    bot.reply_to(msg, summary)
