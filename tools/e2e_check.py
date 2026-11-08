"""端到端流程自检脚本。

逐环节验证：配置 -> 数据库 -> 数据 -> 匹配 -> NL2SQL -> 公司背调 -> 邮件 -> LangGraph 全链路。

用法:
    python tools/e2e_check.py            # 未配置 API Key 时自动用模拟 LLM 验证链路
    python tools/e2e_check.py --fresh    # 先清空 contact_records 并重置候选人状态
    python tools/e2e_check.py --send-mail  # 真实发送一封测试邮件（默认只做 SMTP 登录校验）
"""
import argparse
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

RESULTS: list[tuple[str, str, str]] = []  # (环节, 状态, 详情)


def _import_real_corpus(base_dir: str, limit: int = 120):
    """导入真实公开简历语料快照（离线可用），没有快照则跳过。"""
    snapshot = os.path.join(base_dir, "real_candidates.json")
    if not os.path.exists(snapshot):
        print("[数据] 未找到真实语料快照，跳过硬数据导入")
        return
    import json as _json
    from crawler.resume_corpus_import import import_to_db
    records = _json.load(open(snapshot, encoding="utf-8"))[:limit]
    import_to_db(records)


def record(stage: str, ok: bool | None, detail: str):
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    RESULTS.append((stage, status, detail))
    icon = {"PASS": "[OK]  ", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}[status]
    print(f"{icon} {stage}: {detail}")


def section(title: str):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="清空触达记录并重置候选人状态")
    parser.add_argument("--send-mail", action="store_true", help="真实发送测试邮件")
    args = parser.parse_args()

    import config
    from core.llm import llm

    # ---------- 1. 配置检查 ----------
    section("1. 配置检查")
    key_ok = bool(config.LLM_API_KEY) and "在这里" not in config.LLM_API_KEY
    record("大模型 API Key", True if key_ok else None,
           "已配置" if key_ok else "未配置（本次用模拟 LLM 验证链路，真实生成效果待填入 Key 后复测）")
    record("模型配置", True,
           f"{config.LLM_MODEL} @ {config.LLM_BASE_URL}（备用：{config.LLM_MODEL_FALLBACK or '无'}）")
    record("数据库配置", True,
           f"{config.DB_CONFIG['user']}@{config.DB_CONFIG['host']}:{config.DB_CONFIG['port']}/{config.DB_CONFIG['database']}")
    record("SMTP 配置", True,
           f"{config.SMTP_HOST}:{config.SMTP_PORT} / {config.SMTP_USER}"
           + ("" if config.SMTP_HOST.startswith("smtp.") else "  ← 主机名疑似有误"))

    # 模拟 LLM：未配置 Key 时用固定响应驱动全链路
    if not key_ok:
        from core.llm import GLMClient

        def fake_chat(self, prompt, system=None, temperature=0.3):
            p = prompt or ""
            if "生成一条SQL" in (system or "") or "SQL" in (system or ""):
                if "新增" in p or "插入" in p:
                    return "INSERT INTO candidates (name, email, skill_tags, years_of_exp) VALUES ('测试候选人', 'test@example.com', '[\"Python\"]', 5)"
                if "更新" in p or "修改" in p:
                    return "UPDATE candidates SET status = '待推荐' WHERE id = 1"
                return "SELECT id, name, email, years_of_exp FROM candidates WHERE skill_tags LIKE '%Python%' ORDER BY years_of_exp DESC"
            if "分类" in p:
                return "query"
            return "（模拟回复）候选人技能与职位匹配良好，建议安排初面。"

        GLMClient.available = property(lambda self: True)  # type: ignore
        GLMClient.chat = fake_chat  # type: ignore
        print("[提示] 已启用模拟 LLM，用于验证代码链路而非模型效果。")

    # ---------- 2. 数据库连接与表结构 ----------
    section("2. 数据库连接与表结构")
    try:
        from db import pool
        ver = pool.query("SELECT VERSION() AS v")[0]["v"]
        record("MySQL 连接", True, f"连接成功，版本 {ver}")
    except Exception as e:
        record("MySQL 连接", False, f"{e}")
        print("\n数据库连不上，后续环节无法进行。请确认 MySQL 已启动、.env 密码正确、已执行 db/schema.sql。")
        return summarize()

    expected = {"companies", "positions", "candidates", "contact_records", "crawl_logs", "query_logs"}
    try:
        rows = pool.query(
            "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = %s",
            (config.DB_CONFIG["database"],))
        existing = {r["t"].lower() for r in rows}
        missing = expected - existing
        record("表结构", not missing,
               f"已存在 {len(existing & expected)}/6 张表" + (f"，缺少 {sorted(missing)}" if missing else ""))
        if missing:
            print("请先在 Navicat 中执行 db/schema.sql 建表。")
            return summarize()
    except Exception as e:
        record("表结构", False, str(e))
        return summarize()

    # 外键检查：验证四表关联是否真正生效
    try:
        fks = pool.query(
            "SELECT COUNT(*) AS n FROM information_schema.key_column_usage "
            "WHERE table_schema = %s AND referenced_table_name IS NOT NULL",
            (config.DB_CONFIG["database"],))
        record("外键约束", fks[0]["n"] >= 2, f"检测到 {fks[0]['n']} 个外键关联")
    except Exception as e:
        record("外键约束", None, str(e))

    # ---------- 3. 数据准备 ----------
    section("3. 测试数据")
    if args.fresh:
        pool.execute("DELETE FROM contact_records")
        pool.execute("DELETE FROM candidates")
        pool.execute("DELETE FROM positions")
        pool.execute("DELETE FROM companies")
        print("[清理] 业务四表已清空，将重新导入示例数据")

    cand_n = pool.query("SELECT COUNT(*) AS n FROM candidates")[0]["n"]
    pos_n = pool.query("SELECT COUNT(*) AS n FROM positions")[0]["n"]
    if cand_n == 0 or pos_n == 0:
        print("[导入] 检测到数据为空，导入示例 CSV ...")
        from crawler.dataset_import import import_candidates, import_positions
        base = os.path.join(os.path.dirname(__file__), "..", "data")
        import_candidates(os.path.join(base, "candidates.sample.csv"))
        import_positions(os.path.join(base, "positions.sample.csv"))
        _import_real_corpus(base, limit=120)
        cand_n = pool.query("SELECT COUNT(*) AS n FROM candidates")[0]["n"]
        pos_n = pool.query("SELECT COUNT(*) AS n FROM positions")[0]["n"]
    record("候选人数据", cand_n > 0, f"{cand_n} 条")
    record("职位数据", pos_n > 0, f"{pos_n} 条")

    # ---------- 4. 匹配引擎（纯算法，不写库） ----------
    section("4. 智能匹配引擎")
    try:
        from core import matcher
        position = pool.query(
            "SELECT p.*, c.name AS company_name FROM positions p JOIN companies c ON c.id = p.company_id "
            "WHERE p.status = 1 ORDER BY p.id LIMIT 1")[0]
        position["skill_tags"] = json.loads(position["skill_tags"] or "[]")
        cands = pool.query("SELECT * FROM candidates")
        scored = []
        for c in cands:
            c["skill_tags"] = json.loads(c["skill_tags"] or "[]")
            r = matcher.match(c, position)
            scored.append((c["name"], r["total"], r["skill_score"], r["exp_score"], r["recommend"]))
        scored.sort(key=lambda x: x[1], reverse=True)
        print(f"  职位「{position['title']}」要求技能 {position['skill_tags']}，最低 {position['min_years']} 年")
        for name, total, s, e, rec in scored:
            print(f"    {name}: 总分 {total}（技能 {s} / 经验 {e}）{'→ 推荐' if rec else ''}")
        hit = any(s[4] for s in scored)
        record("匹配打分", hit, f"共计算 {len(scored)} 人，{sum(1 for s in scored if s[4])} 人达 80 分阈值")
    except Exception as e:
        record("匹配打分", False, f"{e}\n{traceback.format_exc()}")

    # ---------- 5. NL2SQL ----------
    section("5. NL2SQL 生成与安全校验")
    try:
        from core.nl2sql import generate_sql, validate_sql
        sql = generate_sql("查询有5年经验的Python候选人")
        if sql:
            print(f"  生成 SQL: {sql}")
            ok, stmt = validate_sql(sql)
            record("SQL 生成", ok, f"类型 {stmt}")
            rows = pool.query(sql)
            record("SQL 执行", True, f"返回 {len(rows)} 行")
        else:
            record("SQL 生成", False, "未生成有效 SQL")
        bad = ["DROP TABLE candidates", "UPDATE candidates SET status='x'", "SELECT 1; DROP TABLE candidates"]
        blocked = [s for s in bad if not validate_sql(s)[0]]
        record("安全校验器", len(blocked) == len(bad), f"{len(blocked)}/{len(bad)} 条危险语句被拦截")
    except Exception as e:
        record("NL2SQL", False, f"{e}\n{traceback.format_exc()}")

    # ---------- 6. 公司背调（真实网络） ----------
    section("6. 公司背调（真实网络请求）")
    try:
        from core.company_lookup import lookup_company
        info = lookup_company("智谱")
        ok = bool(info.get("introduction"))
        detail = f"{info['source_label']}｜{str(info.get('introduction'))[:80]}..."
        record("公司信息获取", ok, detail)
    except Exception as e:
        record("公司信息获取", False, f"{e}")

    # ---------- 7. 邮件模块 ----------
    section("7. 邮件模块")
    try:
        from core.mailer import draft_email
        draft = draft_email({"name": "张三"}, {"title": "AI工程师", "skill_tags": ["Python"]},
                            {"name": "智谱AI"}, None)
        record("邮件草稿生成", bool(draft["subject"] and draft["body"]), f"主题：{draft['subject']}")
    except Exception as e:
        record("邮件草稿生成", False, str(e))

    try:
        import smtplib
        if config.SMTP_PORT == 465:
            s = smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=8)
        else:
            s = smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=8)
            s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.quit()
        record("SMTP 登录", True, "授权码校验通过")
        if args.send_mail:
            from core.mailer import send_with_retry
            ok = send_with_retry(config.SMTP_USER, "【测试】AI猎头助手邮件通道", "这是一封通道测试邮件。")
            record("实际发信", ok, "已发送到自己的邮箱" if ok else "发送失败")
    except Exception as e:
        record("SMTP 登录", False, f"{e}（检查 SMTP_HOST 是否为 smtp.qq.com、授权码是否有效）")

    # ---------- 8. LangGraph 全链路 ----------
    section("8. LangGraph 对话全链路")
    try:
        from graph.builder import app_graph
        cases = [
            ("查一下有5年经验的Python候选人", "query"),
            ("给AI工程师职位推荐候选人", "match"),
            ("智谱公司怎么样", "company_lookup"),
            ("给候选人发面试邀约邮件", "send_email"),
        ]
        for text, expect_intent in cases:
            out = app_graph.invoke({"session_id": "e2e", "user_input": text})
            reply = (out.get("reply") or "").replace("\n", " ")[:110]
            ok = bool(reply)
            record(f"意图 {out.get('intent', '?')}「{text[:10]}…」", ok, reply)
    except Exception as e:
        record("LangGraph 链路", False, f"{e}\n{traceback.format_exc()}")

    # ---------- 9. 数据落库校验 ----------
    section("9. 业务数据落库校验")
    try:
        cr = pool.query("SELECT COUNT(*) AS n FROM contact_records")[0]["n"]
        rec = pool.query("SELECT COUNT(*) AS n FROM candidates WHERE status = '已推荐'")[0]["n"]
        logs = pool.query("SELECT COUNT(*) AS n FROM query_logs")[0]["n"]
        crawl = pool.query("SELECT COUNT(*) AS n FROM crawl_logs")[0]["n"]
        record("contact_records", True, f"{cr} 条推荐记录")
        record("candidates 状态流转", True, f"{rec} 人已置为「已推荐」")
        record("query_logs 审计", logs > 0, f"{logs} 条 SQL 审计记录")
        record("crawl_logs 审计", crawl > 0, f"{crawl} 条爬取记录")
    except Exception as e:
        record("落库校验", False, str(e))

    summarize()


def summarize():
    print(f"\n{'=' * 62}\n汇总\n{'=' * 62}")
    fails = [r for r in RESULTS if r[1] == "FAIL"]
    skips = [r for r in RESULTS if r[1] == "SKIP"]
    print(f"通过 {len(RESULTS) - len(fails) - len(skips)} 项｜失败 {len(fails)} 项｜跳过 {len(skips)} 项")
    if fails:
        print("\n失败项：")
        for stage, _, detail in fails:
            print(f"  - {stage}: {detail.splitlines()[0]}")
    if skips:
        print("\n跳过项：")
        for stage, _, detail in skips:
            print(f"  - {stage}: {detail}")


if __name__ == "__main__":
    main()
