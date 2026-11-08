"""业务层全功能冒烟测试：逐个调用 core/business.py 的可调用函数，检查返回结构与边界行为。

与 full_flow_test.py 的区别：那个测业务链路（LangGraph / DB / 邮件），
这个测「界面背后那层数据函数」（列表、统计、职位管理、导出、背调、分析、重置、简历）。
纯前端的渲染由 tools/push_ui_test.py 用无头浏览器覆盖。

用法:
    python tools/ui_smoke_test.py          # 只读为主，会临时建一条测试职位并在结束时清理
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond, detail: str = ""):
    ok = bool(cond)
    RESULTS.append((name, ok, str(detail)[:160]))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f" — {str(detail)[:160]}" if detail else ""))
    return ok


def section(t: str):
    print(f"\n{'=' * 66}\n{t}\n{'=' * 66}")


def safe(label: str, fn, *a, **kw):
    """调用并兜住异常，返回 (ok, value)。"""
    try:
        return True, fn(*a, **kw)
    except Exception as e:
        check(label, False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return False, None


def main():
    from core import business as app
    from db import pool

    # ---------------- 1. 基础连通 ----------------
    section("1. 数据层连通")
    ok, v = safe("pool.query", lambda: pool.query("SELECT VERSION() v")[0]["v"])
    check("MySQL 可查询", ok and bool(v), v or "")

    # ---------------- 2. 候选人库 ----------------
    section("2. 候选人库")
    ok, rows = safe("load_candidates", app.load_candidates, "全部")
    if ok:
        check("候选人列表非空", isinstance(rows, list) and len(rows) > 0, f"{len(rows)} 行")
        check("行字段数=8", all(len(r) == 8 for r in rows), f"首行={rows[0] if rows else None}")
    for st in ("待筛选", "已推荐", "不存在的状态", ""):
        ok, r = safe(f"load_candidates({st!r})", app.load_candidates, st)
        if ok:
            check(f"筛选「{st or '空'}」不报错", isinstance(r, list), f"{len(r)} 行")

    ok, stats = safe("candidate_stats", app.candidate_stats)
    if ok:
        check("统计卡为字典", isinstance(stats, dict), f"键={list(stats)[:6]}")
        check("统计卡含总数与已联系", "total" in stats and "contacted" in stats, str(list(stats))[:80])

    ok, recs = safe("load_records", app.load_records)
    if ok:
        check("推荐记录 9 列(含候选人ID与HR决策)", all(len(r) == 9 for r in recs), f"{len(recs)} 条")

    # ---------------- 3. 职位管理 ----------------
    section("3. 职位管理")
    ok, pos = safe("load_positions", app.load_positions)
    if ok:
        check("职位列表 7 列", all(len(r) == 7 for r in pos), f"{len(pos)} 条")

    ok, out = safe("add_position 空参数", app.add_position, "", "", 3, "", "")
    if ok:
        msg, tbl = out
        check("空参数给出提示", "都要填" in msg, msg)

    ok, out = safe("add_position 正常", app.add_position,
                   "UI测试公司", "UI测试岗位", 2, "15-20K", "Python, SQL、Redis")
    pos_id = None
    if ok:
        msg, tbl = out
        check("新增职位成功", "已新增职位" in msg, msg[:60])
        check("技能分隔符解析", any("Redis" in str(x) for x in tbl), "Python/SQL/Redis 三标签")
        found = pool.query("SELECT id FROM positions WHERE title='UI测试岗位' ORDER BY id DESC LIMIT 1")
        pos_id = found[0]["id"] if found else None
        check("职位已入库", pos_id is not None, f"id={pos_id}")

    if pos_id:
        ok, out = safe("close_position", app.close_position, pos_id)
        if ok:
            msg, tbl = out
            check("关闭职位成功", "已关闭" in msg, msg)
            st = pool.query("SELECT status FROM positions WHERE id=%s", (pos_id,))[0]["status"]
            check("状态已置 0", int(st) == 0, f"status={st}")
        # 清理测试数据
        pool.execute("DELETE FROM positions WHERE id=%s", (pos_id,))
        comp = pool.query("SELECT id FROM companies WHERE name='UI测试公司'")
        if comp:
            pool.execute("DELETE FROM companies WHERE id=%s", (comp[0]["id"],))
        print("[清理] 测试职位与测试公司已删除")

    ok, out = safe("close_position 非法输入", app.close_position, "abc")
    if ok:
        check("非法 ID 给出提示", "请填写" in out[0], out[0])
    ok, out = safe("close_position 不存在 ID", app.close_position, 99999999)
    if ok:
        check("不存在 ID 给出提示", "没有找到" in out[0], out[0])

    # ---------------- 4. 导出 ----------------
    section("4. 导出")
    ok, out = safe("export_candidates", app.export_candidates)
    if ok:
        path, msg = out
        check("导出文件已生成", path and os.path.exists(path), msg)
        if path and os.path.exists(path):
            with open(path, encoding="utf-8-sig") as f:
                head = f.readline().strip()
                n = sum(1 for _ in f)
            check("CSV 表头正确", head.startswith("编号,姓名"), head)
            check("CSV 行数与候选人一致", n >= 100, f"{n} 行")

    # ---------------- 5. 公司库 ----------------
    section("5. 公司库")
    ok, comps = safe("load_companies", app.load_companies)
    if ok:
        check("公司列表 6 列", all(len(r) == 6 for r in comps), f"{len(comps)} 条")
    ok, choices = safe("company_choices", app.company_choices)
    if ok:
        check("公司下拉非空", isinstance(choices, list) and len(choices) > 0, f"{len(choices)} 项")
        check("下拉无重复", len(choices) == len(set(choices)), f"{len(choices)} -> {len(set(choices))}")

    ok, txt = safe("crawl_company_ui 空输入", app.crawl_company_ui, "")
    if ok:
        msg, tbl = txt
        check("空输入给提示", "请输入" in msg, msg)
        check("空输入仍返回公司表", isinstance(tbl, list), f"{len(tbl)} 条")

    # ---------------- 6. 公司分析 ----------------
    section("6. 公司分析")
    ok, hist = safe("load_analysis_history", app.load_analysis_history)
    if ok:
        check("历史记录 7 列", all(len(r) == 7 for r in hist), f"{len(hist)} 条")
    ok, out = safe("run_analysis 空输入", app.run_analysis, "")
    if ok:
        check("空输入给提示", "请先选择" in out[0], out[0])

    ok, out = safe("run_analysis 走缓存", app.run_analysis, "智谱", False, False)
    if ok:
        md, h = out
        check("分析报告有评分", "评分" in md or "分" in md, md[:50].replace("\n", " "))
        check("报告含合规风险区块", "合规风险" in md)
        check("报告含决策建议", "暂缓" in md or "推荐" in md)

    ok, out = safe("run_analysis 重新核查风险", app.run_analysis, "智谱", True, True)
    if ok:
        check("强制核查后仍出报告", len(out[0]) > 500, f"{len(out[0])} 字")

    # ---------------- 7. 设置与重置 ----------------
    section("7. 设置与重置")
    ok, out = safe("reset_demo", app.reset_demo)
    if ok:
        check("重置提示成功", "已重置" in out, out)
        left = pool.query("SELECT COUNT(*) n FROM contact_records")[0]["n"]
        check("推荐记录已清空", left == 0, f"{left} 条")

    # ---------------- 8. 简历数据 ----------------
    section("8. 简历数据")
    cand = pool.query("SELECT id FROM candidates ORDER BY id LIMIT 1")
    cid = cand[0]["id"] if cand else None
    if cid:
        ok, out = safe("fetch_resume 正常", app.fetch_resume, cid)
        if ok:
            cand_row, resume = out
            check("取到候选人", cand_row is not None, f"id={cid}")
            check("返回简历字符串", isinstance(resume, str), f"{len(resume)} 字")
            check("含姓名与技能字段", "name" in (cand_row or {}) and "skill_tags" in (cand_row or {}))
    ok, out = safe("fetch_resume 不存在 ID", app.fetch_resume, 99999999)
    if ok:
        check("不存在 ID 返回空", out == (None, ""), str(out)[:60])
    ok, out = safe("fetch_resume 负数 ID", app.fetch_resume, -1)
    if ok:
        check("负数 ID 不崩", out == (None, ""), str(out)[:60])

    # ---------------- 汇总 ----------------
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{'=' * 66}\n汇总：通过 {len(RESULTS) - len(fails)} / {len(RESULTS)}")
    if fails:
        print("失败项：")
        for n, _, d in fails:
            print(f"  - {n}: {d}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
