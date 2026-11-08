"""从简历文本回填 candidates.gender。

识别规则（按优先级）：
1. 「先生」 -> 男；「女士/小姐」 -> 女
2. 顿号/逗号分隔的性别标注：……，男，……
3. 「性别：男 / 性别:女」

只更新 gender IS NULL 的记录，可重复执行（幂等）。
用法: python tools/backfill_gender.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import pool  # noqa: E402

_PATTERNS = [
    ("男", re.compile(r"先生")),
    ("女", re.compile(r"女士|小姐")),
    ("男", re.compile(r"[，,、]\s*男\s*[，,、]|性别\s*[:：]\s*男")),
    ("女", re.compile(r"[，,、]\s*女\s*[，,、]|性别\s*[:：]\s*女")),
]


def infer_gender(text: str) -> str | None:
    for gender, pat in _PATTERNS:
        if pat.search(text or ""):
            return gender
    return None


def main() -> int:
    rows = pool.query(
        "SELECT id, name, resume_text FROM candidates WHERE gender IS NULL")
    updated = {"男": 0, "女": 0}
    for r in rows:
        g = infer_gender(r["resume_text"])
        if g:
            pool.execute("UPDATE candidates SET gender = %s WHERE id = %s", (g, r["id"]))
            updated[g] += 1
    left = pool.query("SELECT COUNT(*) n FROM candidates WHERE gender IS NULL")[0]["n"]
    total = pool.query("SELECT COUNT(*) n FROM candidates")[0]["n"]
    print(f"回填完成：男 {updated['男']} 人，女 {updated['女']} 人；"
          f"仍无法识别 {left}/{total}（保持 NULL，页面显示「未填写」）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
