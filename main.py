import os
import re
import math
import telebot
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

# 初始化环境变量
TOKEN = os.getenv('TOKEN')
DATABASE_URL = os.getenv('DATABASE_URL')

# 初始化 Bot 和数据库连接
bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
cursor = conn.cursor()

# 建表：settings 包含 chat_id 与 user_id 联合主键
cursor.execute('''
CREATE TABLE IF NOT EXISTS settings (
    chat_id BIGINT,
    user_id BIGINT,
    currency TEXT DEFAULT 'RMB',
    rate DOUBLE PRECISION DEFAULT 0,
    fee_rate DOUBLE PRECISION DEFAULT 0,
    commission_rate DOUBLE PRECISION DEFAULT 0,
    PRIMARY KEY (chat_id, user_id)
)''')
# 确保 settings 使用 chat_id 与 user_id 联合主键
# 先删除旧主键，然后添加新的
cursor.execute("""
DO $$
BEGIN
  ALTER TABLE settings DROP CONSTRAINT IF EXISTS settings_pkey;
EXCEPTION WHEN undefined_object THEN NULL;
END$$;
""")
cursor.execute("""
DO $$
BEGIN
  ALTER TABLE settings ADD PRIMARY KEY (chat_id, user_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END$$;
""")
conn.commit()()

# 数字向上保留两位小数
...
