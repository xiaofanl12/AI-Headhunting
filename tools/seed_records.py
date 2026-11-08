"""演示种子：建一个在招职位，给匹配度靠前的候选人各生成一条推荐记录。

用途：重置演示数据（tools/ui_smoke_test.py 会把推荐记录清空）之后，
让决策台重新有内容可演。幂等：先清空 contact_records 再重建。

用法:
    python tools/seed_records.py [条数，默认 6]
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import matcher  # noqa: E402
from db import pool  # noqa: E402

TOP_N = int(sys.argv[1]) if len(sys.argv) > 1 else 6


def _tags(raw):
    """skill_tags 在库里是 JSON 字符串，打分器需要的是列表。"""
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw or "[]")
        return val if isinstance(val, list) else []
    except Exception:
        return []


def main() -> int:
    # 1) 找一个在招职位，没有就建一个
    pos = pool.query(
        "SELECT p.id, p.title, c.name FROM positions p JOIN companies c ON c.id=p.company_id "
        "WHERE p.status=1 ORDER BY p.id LIMIT 1")
    if pos:
        pid, title, comp = pos[0]["id"], pos[0]["title"], pos[0]["name"]
        print(f"复用职位：{comp} · {title}（id={pid}）")
    else:
        comp_row = pool.query("SELECT id, name FROM companies ORDER BY id LIMIT 1")
        if not comp_row:
            print("公司库为空，先跑一次公司背调再试。")
            return 1
        cid, comp = comp_row[0]["id"], comp_row[0]["name"]
        pool.execute(
            "INSERT INTO positions (company_id, title, skill_tags, min_years, salary_range, status) "
            "VALUES (%s,%s,%s,%s,%s,1)",
            (cid, "AI 应用工程师", '["Python","LangChain","RAG","MySQL"]', 3, "25-40K"))
        pid = pool.query("SELECT id FROM positions ORDER BY id DESC LIMIT 1")[0]["id"]
        title = "AI 应用工程师"
        print(f"新建职位：{comp} · {title}（id={pid}）")

    row = pool.query("SELECT skill_tags, min_years FROM positions WHERE id=%s", (pid,))[0]
    position = {
        "title": title,
        "skill_tags": _tags(row["skill_tags"]),
        "min_years": row["min_years"] or 0,
    }

    # 2) 取有邮箱的候选人，逐个打分（skill_tags 同样要先解析成列表）
    cands = pool.query(
        "SELECT id, name, skill_tags, years_of_exp, education FROM candidates "
        "WHERE COALESCE(email,'') <> '' ORDER BY id")
    scored = []
    for c in cands:
        c = dict(c)
        c["skill_tags"] = _tags(c["skill_tags"])
        r = matcher.match(c, position)
        # 只收「算法认为可推荐」的：低于阈值的候选人不该出现在决策台里
        if r["recommend"]:
            scored.append((r["total"], c, r))
    if not scored:
        print("没有达到推荐阈值的候选人。可降低职位 min_years，或在「匹配策略」里调低推荐阈值后重试。")
        return 1
    scored.sort(key=lambda x: -x[0])
    picked = scored[:TOP_N]

    # 3) 重建推荐记录
    pool.execute("DELETE FROM contact_records")
    for total, c, r in picked:
        pool.execute(
            "INSERT INTO contact_records (candidate_id, position_id, match_score, skill_score, "
            "exp_score, reason, send_status) VALUES (%s,%s,%s,%s,%s,%s,'pending')",
            (c["id"], pid, r["total"], r.get("skill_score", 0), r.get("exp_score", 0),
             matcher.build_reason(c, position, r, None)))
    print(f"已写入 {len(picked)} 条推荐记录：")
    for total, c, _ in picked:
        print(f"  {c['name']:<8} {total:>5.1f} 分")
    print("\n提示：打开 http://127.0.0.1:7862 → 决策台，即可点「推送」演示完整链路。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
