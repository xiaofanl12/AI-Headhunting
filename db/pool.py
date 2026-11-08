"""MySQL 连接池：Gradio 多会话并发安全。"""
import mysql.connector
from mysql.connector import pooling

from config import DB_CONFIG

_pool: pooling.MySQLConnectionPool | None = None


def get_pool() -> pooling.MySQLConnectionPool:
    global _pool
    if _pool is None:
        _pool = pooling.MySQLConnectionPool(
            pool_name="headhunter_pool",
            pool_size=8,
            pool_reset_session=True,
            **DB_CONFIG,
        )
    return _pool


def get_connection():
    return get_pool().get_connection()


def query(sql: str, params: tuple = (), timeout_ms: int = 5000) -> list[dict]:
    """执行 SELECT，返回字典列表。

    timeout_ms: 单条 SELECT 的执行超时（毫秒），由 MySQL 8 的 max_execution_time 强制中断，
    防止一条慢查询（全表扫/笛卡尔积）长期占用连接池。
    """
    conn = get_connection()
    try:
        cur = conn.cursor(dictionary=True)
        if timeout_ms and timeout_ms > 0:
            cur.execute("SET SESSION max_execution_time = %s", (timeout_ms,))
        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    finally:
        conn.close()


def execute(sql: str, params: tuple = ()) -> int:
    """执行 INSERT/UPDATE/DELETE，返回受影响行数（自动提交）。"""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        affected = cur.rowcount
        cur.close()
        return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def executemany(sql: str, seq_params: list[tuple]) -> int:
    """批量执行同一条 SQL（单事务提交），返回累计受影响行数。

    用于重算匹配这类「一次改上百行」的场景，把 N 次网络往返压成 1 次。
    """
    if not seq_params:
        return 0
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.executemany(sql, seq_params)
        conn.commit()
        affected = cur.rowcount
        cur.close()
        return affected
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
