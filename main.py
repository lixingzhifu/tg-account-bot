1. # —— 导入与配置 —— #
2. import os
2. import re
2. import psycopg2
2. from psycopg2.extras import RealDictCursor
2. from telebot import TeleBot, types
2. import pytz
2. from datetime import datetime, timedelta

3. # —— 环境变量 —— #
4. TOKEN = os.getenv("TOKEN")
4. DATABASE_URL = os.getenv("DATABASE_URL")
4. CUSTOMER_HELP_URL = "https://your.support.link"
4. CUSTOMER_CUSTOM_URL = "https://your.custom.link"

5. # —— 初始化Bot与数据库连接 —— #
6. bot = TeleBot(TOKEN)
6. conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
6. cursor = conn.cursor()

7. # —— 建表：settings 和 transactions —— #
8. cursor.execute("""
8. CREATE TABLE IF NOT EXISTS settings (
8.     chat_id BIGINT NOT NULL,
8.     user_id BIGINT NOT NULL,
8.     currency TEXT NOT NULL,
8.     rate DOUBLE PRECISION NOT NULL,
8.     fee_rate DOUBLE PRECISION NOT NULL,
8.     commission_rate DOUBLE PRECISION NOT NULL,
8.     PRIMARY KEY(chat_id, user_id)
8. );
8. """)
8. cursor.execute("""
8. CREATE TABLE IF NOT EXISTS transactions (
8.     id SERIAL PRIMARY KEY,
8.     chat_id BIGINT NOT NULL,
8.     user_id BIGINT NOT NULL,
8.     name TEXT NOT NULL,
8.     action TEXT NOT NULL CHECK(action IN ('deposit','delete','issue','delete_issue')),
8.     amount DOUBLE PRECISION NOT NULL,
8.     after_fee DOUBLE PRECISION NOT NULL,
8.     commission_rmb DOUBLE PRECISION NOT NULL,
8.     commission_usdt DOUBLE PRECISION NOT NULL,
8.     deducted_amount DOUBLE PRECISION NOT NULL,
8.     deducted_usdt DOUBLE PRECISION NOT NULL,
8.     rate DOUBLE PRECISION NOT NULL,
8.     fee_rate DOUBLE PRECISION NOT NULL,
8.     commission_rate DOUBLE PRECISION NOT NULL,
8.     currency TEXT NOT NULL,
8.     date TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
8. );
8. """)
8. conn.commit()

9. # —— 工具函数 —— #
10. def rollback():
10.     try:
10.         conn.rollback()
10.     except:
10.         pass

11. # —— /start —— #
12. @bot.message_handler(commands=['start'])
12. def cmd_start(msg):
12.     kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
12.     kb.add('/trade', '/commands')
12.     kb.add('/reset', '/show')
12.     kb.add('/help_customer', '/custom')
12.     bot.reply_to(msg, "欢迎使用 LX 记账机器人 ✅\n请选择菜单：", reply_markup=kb)

13. # —— /commands —— #
14. @bot.message_handler(commands=['commands'])
14. def cmd_commands(msg):
14.     text = (
14.         "📖 指令大全：\n"
14.         "/start - 启动机器人\n"
14.         "/trade - 设置交易参数\n"
14.         "/reset - 清空所有记录\n"
14.         "/show - 显示今日账单\n"
14.         "+1000 或 入笔1000 - 记入款\n"
14.         "删除1000 或 撤销入款1000 - 删除入款\n"
14.         "下发1000 或 下发-1000 - 记录/撤销下发\n"
14.         "/help_customer - 客服帮助\n"
14.         "/custom - 定制机器人\n"
14.     )
14.     bot.reply_to(msg, text)

15. # —— /trade —— #
16. @bot.message_handler(commands=['trade'])
16. def cmd_trade(msg):
16.     bot.reply_to(msg,
16.         "请按格式发送：\n"
16.         "设置交易指令\n"
16.         "设置货币：RMB\n"
16.         "设置汇率：0\n"
16.         "设置费率：0\n"
16.         "中介佣金：0.0"
16.     )

17. @bot.message_handler(func=lambda m: '设置交易指令' in (m.text or ''))
17. def handle_trade_setup(msg):
17.     text = msg.text
17.     try:
17.         curr = re.search(r'设置货币[:：]\s*(\S+)', text).group(1)
17.         rate = float(re.search(r'设置汇率[:：]\s*([0-9.]+)', text).group(1))
17.         fee  = float(re.search(r'设置费率[:：]\s*([0-9.]+)', text).group(1))
17.         comm = float(re.search(r'中介佣金[:：]\s*([0-9.]+)', text).group(1))
17.     except:
17.         return bot.reply_to(msg, "❌ 格式错误，请严格按指示填写。")
17.     cid, uid = msg.chat.id, msg.from_user.id
17.     try:
17.         cursor.execute(
17.             "INSERT INTO settings(chat_id,user_id,currency,rate,fee_rate,commission_rate)"
17.             " VALUES(%s,%s,%s,%s,%s,%s)"
17.             " ON CONFLICT(chat_id,user_id) DO UPDATE SET"
17.             " currency=EXCLUDED.currency, rate=EXCLUDED.rate,"
17.             " fee_rate=EXCLUDED.fee_rate, commission_rate=EXCLUDED.commission_rate",
17.             (cid, uid, curr, rate, fee, comm)
17.         )
17.         conn.commit()
17.         bot.reply_to(msg, f"✅ 设置成功\n货币：{curr}\n汇率：{rate}\n费率：{fee}%\n佣金率：{comm}%")
17.     except Exception as e:
17.         rollback()
17.         bot.reply_to(msg, f"❌ 存储失败：{e}")

18. # —— /reset —— #
19. @bot.message_handler(commands=['reset'])
19. def cmd_reset(msg):
19.     cid, uid = msg.chat.id, msg.from_user.id
19.     try:
19.         cursor.execute(
19.             "DELETE FROM transactions WHERE chat_id=%s AND user_id=%s",
19.             (cid, uid)
19.         )
19.         conn.commit()
19.         bot.reply_to(msg, "✅ 记录已清零！")
19.     except Exception as e:
19.         rollback()
19.         bot.reply_to(msg, f"❌ 重置失败：{e}")

20. # —— /show —— #
21. @bot.message_handler(commands=['show'])
21. def cmd_show(msg):
21.     cid, uid = msg.chat.id, msg.from_user.id
21.     tz = pytz.timezone('Asia/Kuala_Lumpur')
21.     today = datetime.now(tz).date()
21.     try:
21.         cursor.execute(
21.             "SELECT * FROM transactions WHERE chat_id=%s AND user_id=%s ORDER BY date",
21.             (cid, uid)
21.         )
21.         rows = cursor.fetchall()
21.     except Exception as e:
21.         rollback(); return bot.reply_to(msg, f"❌ 查询失败：{e}")
21.     dep_lines = []
21.     iss_lines = []
21.     total_dep = total_pending = total_comm = total_iss = 0.0
21.     for r in rows:
21.         dt = r['date']
21.         if dt.tzinfo is None: dt = dt.replace(tzinfo=pytz.utc)
21.         local = dt.astimezone(tz)
21.         if r['action']=='deposit': total_dep += r['amount']; total_pending += r['after_fee']; total_comm += r['commission_rmb']
21.         elif r['action']=='delete': total_dep -= r['amount']; total_pending -= r['after_fee']; total_comm -= r['commission_rmb']
21.         elif r['action']=='issue': total_iss += r['deducted_amount']
21.         elif r['action']=='delete_issue': total_iss -= r['deducted_amount']
21.         if local.date()==today:
21.             ts = local.strftime('%H:%M:%S')
21.             if r['action'] in ('deposit','delete'):
21.                 sign = '+' if r['action']=='deposit' else '-'
21.                 usd = round(r['after_fee']/r['rate'],2)
21.                 dep_lines.append(f"{r['id']:03d}. {ts} {sign}{abs(r['amount'])} * {1-r['fee_rate']/100} / {r['rate']} = {usd}  {r['name']}")
21.             else:
21.                 sign = '+' if r['action']=='issue' else '-'
21.                 ud = round(r['deducted_amount']/r['rate'],2)
21.                 iss_lines.append(f"{ts} {sign}{r['deducted_amount']} | {sign}{ud}(USDT)  {r['name']}")
21.     tp = total_pending - total_iss
21.     text = [f"日入笔（{len(dep_lines)}笔）"] + (dep_lines or ["无"]) + ["\n今日下发（%d笔）"%len(iss_lines)] + (iss_lines or ["无"])
21.     text += ["\n汇总：", f"已入款：{total_dep}(RMB)", f"应下发：{total_pending}(RMB)", f"已下发：{total_iss}(RMB)", f"未下发：{tp}(RMB)", f"累计佣金：{total_comm}(RMB)"]
21.     bot.reply_to(msg, "\n".join(text))

22. # —— 客服 & 定制 —— #
23. @bot.message_handler(commands=['help_customer'])
23. def cmd_help(msg): bot.reply_to(msg, f"客服帮助：{CUSTOMER_HELP_URL}")
23. @bot.message_handler(commands=['custom'])
23. def cmd_custom(msg): bot.reply_to(msg, f"定制机器人：{CUSTOMER_CUSTOM_URL}")

24. # —— 统一行动入口 —— #
25. @bot.message_handler(func=lambda m: re.match(r'^(?:[\+入笔]?\d+(?:\.\d+)?|删除\d+(?:\.\d+)?|下发-?\d+(?:\.\d+)?|删除下发\d+(?:\.\d+)?)$', m.text or ''))
25. def handle_action(msg):
25.     text = msg.text.strip()
25.     m_dep = re.match(r'^(?:[\+入笔]?)(\d+(?:\.\d+)?)$', text)
25.     m_del = re.match(r'^(?:删除|撤销入款|入款-)(\d+(?:\.\d+)?)$', text)
25.     m_iss = re.match(r'^下发(-?\d+(?:\.\d+)?)$', text)
25.     m_idel= re.match(r'^删除下发(\d+(?:\.\d+)?)$', text)
25.     cid, uid = msg.chat.id, msg.from_user.id
25.     cursor.execute("SELECT * FROM settings WHERE chat_id=%s AND user_id=%s", (cid, uid))
25.     s = cursor.fetchone()
25.     if not s: return bot.reply_to(msg, "❌ 请先 /trade 设置参数。")
25.     tz = pytz.timezone('Asia/Kuala_Lumpur'); now = datetime.now(tz)
25.     if m_dep: amt = float(m_dep.group(1)); action='deposit'
25.     elif m_del: amt = float(m_del.group(1)); action='delete'
25.     elif m_iss: amt = float(m_iss.group(1)); action='issue'
25.     else: amt = float(m_idel.group(1)); action='delete_issue'
25.     after = amt*(1-s['fee_rate']/100)
25.     cr = s['commission_rate']/100*amt
25.     cu = round(cr/s['rate'],2)
25.     da = after if action in ('deposit','delete') else abs(after)
25.     du = round(da/s['rate'],2)
25.     ded = amt if action in ('issue','delete_issue') else 0.0
25.     dedu= round(ded/s['rate'],2)
25.     try:
25.         cursor.execute("""
25. INSERT INTO transactions(chat_id,user_id,name,action,amount,after_fee,commission_rmb,commission_usdt,deducted_amount,deducted_usdt,rate,fee_rate,commission_rate,currency)
25. VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
25. """,
25.             (cid, uid, msg.from_user.username, action, amt, after, cr, cu, ded, dedu, s['rate'], s['fee_rate'], s['commission_rate'], s['currency'])
25.         )
25.         conn.commit()
25.     except Exception as e:
25.         rollback(); return bot.reply_to(msg, f"❌ 存储失败：{e}")
25.     bot.reply_to(msg, f"✅ 已{ '删除' if action in ('delete','delete_issue') else '' }{ '下发' if action in ('issue','delete_issue') else '入' }款 {amt} ({s['currency']})")

26. # —— 启动轮询 —— #
26. if __name__ == '__main__':
26.     bot.remove_webhook()
26.     bot.infinity_polling(skip_pending=True)
