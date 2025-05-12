import pytz
from datetime import datetime

# —— 入账（记录交易） —— #
@bot.message_handler(func=lambda m: re.match(r'^[\+入笔]*\d+(\.\d+)?$', m.text or ''))
def handle_deposit(msg):
    # 获取用户的 chat_id 和 user_id
    chat_id = msg.chat.id
    user_id = msg.from_user.id

    # 检查是否已经设置交易参数
    cursor.execute("SELECT * FROM settings WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    settings = cursor.fetchone()
    if not settings:
        return bot.reply_to(msg, "❌ 请先“设置交易”并填写汇率，才能入账。")

    # 使用更严格的正则来提取金额
    match = re.findall(r'[\+入笔]*([0-9]+(\.\d+)?)', msg.text)
    if not match:
        return bot.reply_to(msg, "❌ 无效的入账格式。请输入有效的金额，示例：+1000 或 入1000")

    # 提取金额并转换为浮动类型
    amount = float(match[0][0])  # 提取并转换金额

    # 获取当前设置的交易参数
    currency = settings['currency']
    rate = settings['rate']
    fee_rate = settings['fee_rate']
    commission_rate = settings['commission_rate']

    # 获取当前时间（马来西亚时区）
    malaysia_tz = pytz.timezone('Asia/Kuala_Lumpur')
    time_now = datetime.now(malaysia_tz).strftime('%H:%M:%S')

    # 获取用户名或ID
    username = msg.from_user.username if msg.from_user.username else str(user_id)

    # 计算下发金额和佣金
    amount_after_fee = amount * (1 - fee_rate / 100)
    amount_in_usdt = round(amount_after_fee / rate, 2)  # 向上取二位
    commission_rmb = round(amount * (commission_rate / 100), 2)
    commission_usdt = round(commission_rmb / rate, 2)

    # 生成编号（简单的序列号）
    cursor.execute("SELECT COUNT(*) FROM transactions WHERE chat_id = %s AND user_id = %s", (chat_id, user_id))
    transaction_count = cursor.fetchone()['count'] + 1
    transaction_id = str(transaction_count).zfill(3)

    # 存储入账记录
    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, username, amount, rate, fee_rate, commission_rate, currency))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 生成返回信息
    result = (
        f"✅ 已入款 +{amount} ({currency})\n"
        f"编号：{transaction_id}\n"
        f"{transaction_id}. {time_now} {amount} * {1 - fee_rate / 100} / {rate} = {amount_in_usdt}  {username}\n"
    )

    if commission_rate > 0:
        result += (
            f"{transaction_id}. {time_now} {amount} * {commission_rate / 100} = {commission_rmb} 【佣金】\n"
        )

    result += (
        f"已入款（{transaction_count}笔）：{amount} ({currency})\n"
        f"总入款金额：{amount} ({currency})\n"
        f"汇率：{rate}\n"
        f"费率：{fee_rate}%\n"
    )

    if commission_rate > 0:
        result += f"佣金：{commission_rmb} ({currency}) | {commission_usdt} USDT\n"
    else:
        result += "佣金：0.0 (RMB) | 0.0 USDT\n"

    result += (
        f"应下发：{amount_after_fee} ({currency}) | {amount_in_usdt} (USDT)\n"
        f"已下发：0.0 ({currency}) | 0.00 (USDT)\n"
        f"未下发：{amount_after_fee} ({currency}) | {amount_in_usdt} (USDT)\n"
    )

    if commission_rate > 0:
        result += f"中介佣金应下发：{commission_rmb} ({currency}) | {commission_usdt} (USDT)\n"

    bot.reply_to(msg, result)
