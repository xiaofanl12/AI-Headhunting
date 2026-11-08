"""执行 SQL 迁移文件（Navicat 之外的备用方式）。

用法: python tools/apply_migration.py db/migrations/V2__p0_fixes.sql
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import mysql.connector

from config import DB_CONFIG


def main(path: str):
    sql_text = open(path, encoding="utf-8").read()
    # 去掉注释行后按分号切分
    lines = [ln for ln in sql_text.splitlines() if not ln.strip().startswith("--")]
    statements = [s.strip() for s in re.split(r";\s*\n", "\n".join(lines)) if s.strip()]

    conn = mysql.connector.connect(**DB_CONFIG)
    cur = conn.cursor()
    for stmt in statements:
        head = " ".join(stmt.split())[:70]
        try:
            cur.execute(stmt)
            print(f"[OK]   {head}")
        except mysql.connector.Error as e:
            print(f"[SKIP] {head} -> {e.msg}")
    conn.commit()
    cur.close()
    conn.close()
    print("迁移执行完毕")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "db/migrations/V2__p0_fixes.sql")
