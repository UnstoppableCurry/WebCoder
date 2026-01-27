# -*- coding: utf-8 -*-
"""
SQLite 数据库连接管理
"""

import sqlite3
import aiosqlite
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import DATABASE_PATH

# 同步数据库连接 (用于初始化)
def get_sync_db() -> sqlite3.Connection:
    """获取同步数据库连接"""
    conn = sqlite3.connect(str(DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    return conn

# 异步数据库连接
@asynccontextmanager
async def get_db():
    """获取异步数据库连接"""
    db = await aiosqlite.connect(str(DATABASE_PATH))
    db.row_factory = aiosqlite.Row
    try:
        yield db
    finally:
        await db.close()

class Database:
    """数据库操作封装类"""

    def __init__(self):
        self.db_path = str(DATABASE_PATH)

    @asynccontextmanager
    async def connection(self):
        """获取异步连接"""
        db = await aiosqlite.connect(self.db_path)
        db.row_factory = aiosqlite.Row
        try:
            yield db
        finally:
            await db.close()

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """执行 SQL，返回 lastrowid"""
        async with self.connection() as db:
            cursor = await db.execute(sql, params)
            await db.commit()
            return cursor.lastrowid

    async def execute_many(self, sql: str, params_list: list):
        """批量执行"""
        async with self.connection() as db:
            await db.executemany(sql, params_list)
            await db.commit()

    async def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """查询单条"""
        async with self.connection() as db:
            cursor = await db.execute(sql, params)
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def fetch_all(self, sql: str, params: tuple = ()) -> list:
        """查询多条"""
        async with self.connection() as db:
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

# 全局数据库实例
db = Database()

def init_db():
    """初始化数据库表"""
    migrations_dir = Path(__file__).parent / "migrations"

    conn = get_sync_db()
    cursor = conn.cursor()

    # 创建迁移记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT UNIQUE NOT NULL,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 获取已应用的迁移
    cursor.execute("SELECT filename FROM _migrations")
    applied = {row[0] for row in cursor.fetchall()}

    # 执行新的迁移
    if migrations_dir.exists():
        for migration_file in sorted(migrations_dir.glob("*.sql")):
            if migration_file.name not in applied:
                print(f"[DB] 应用迁移: {migration_file.name}")
                with open(migration_file, 'r', encoding='utf-8') as f:
                    sql = f.read()
                cursor.executescript(sql)
                cursor.execute(
                    "INSERT INTO _migrations (filename) VALUES (?)",
                    (migration_file.name,)
                )

    conn.commit()
    conn.close()
    print(f"[DB] 数据库初始化完成: {DATABASE_PATH}")
