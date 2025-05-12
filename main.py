# —— 入账命令 —— #
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

    # 解析入账金额
    amount = float(re.findall(r'\d+(\.\d+)?', msg.text)[0])

    # 获取当前设置的交易参数
    currency = settings['currency']
    rate = settings['rate']
    fee_rate = settings['fee_rate']
    commission_rate = settings['commission_rate']

    # 计算下发金额和佣金
    amount_after_fee = amount * (1 - fee_rate / 100)
    amount_in_usdt = round(amount_after_fee / rate, 2)  # 向上取二位
    commission_rmb = round(amount * (commission_rate / 100), 2)
    commission_usdt = round(commission_rmb / rate, 2)

    # 存储入账记录
    try:
        cursor.execute("""
        INSERT INTO transactions (chat_id, user_id, name, amount, rate, fee_rate, commission_rate, currency)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, user_id, msg.from_user.username, amount, rate, fee_rate, commission_rate, currency))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return bot.reply_to(msg, f"❌ 存储失败：{e}")

    # 返回入账信息
    bot.reply_to(msg, 
        f"✅ 已入款 {amount} ({currency})\n"
        f"实际下发金额：{amount_after_fee} ({currency})\n"
        f"应下发：{amount_in_usdt} USDT\n"
        f"佣金：{commission_rmb} ({currency}) | {commission_usdt} USDT"
    )
