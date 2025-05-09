import aiosqlite
import os

DB_PATH = os.getenv("DATABASE_PATH", "bot.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                user_id INTEGER PRIMARY KEY,
                currency TEXT DEFAULT 'RMB',
                rate REAL DEFAULT 9.0,
                fee REAL DEFAULT 2.0,
                commission REAL DEFAULT 0.5
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                name TEXT,
                amount REAL,
                fee_amount REAL,
                usdt REAL,
                comm REAL,
                date TEXT
            )
        """)
        await db.commit()

async def get_settings(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT currency, rate, fee, commission FROM settings WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return dict(currency=row[0], rate=row[1], fee=row[2], commission=row[3])
        else:
            await db.execute("INSERT INTO settings(user_id) VALUES (?)", (user_id,))
            await db.commit()
            return dict(currency='RMB', rate=9.0, fee=2.0, commission=0.5)

async def update_setting(user_id, key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE settings SET {key} = ? WHERE user_id = ?", (value, user_id))
        await db.commit()

async def add_record(user_id, amount, fee_amount, usdt, comm, name, date):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO transactions(user_id, amount, fee_amount, usdt, comm, name, date)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, amount, fee_amount, usdt, comm, name, date))
        await db.commit()

async def get_records(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT amount, fee_amount, usdt, comm, name, date FROM transactions WHERE user_id = ?", (user_id,))
        rows = await cursor.fetchall()
        return [dict(amount=r[0], fee=r[1], usdt=r[2], comm=r[3], name=r[4], date=r[5]) for r in rows]

async def reset_user_data(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
        await db.commit()
