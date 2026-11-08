"""业务函数层：职位管理、候选人导出、公司背调入库、公司分析、演示重置。

历史说明：这些函数最早住在 app.py（旧 Gradio 界面的后端）。旧界面下线后，
新前端（web/server.py）仍需要同一套数据操作，于是把它们原样搬到这里，
返回结构保持不变——旧测试脚本（tools/ui_smoke_test.py）也因此无需大改。

约定：函数返回「界面可直接展示」的结构（列表行 / 提示文案），
不做 HTTP、不拼 HTML；鉴权与参数校验由 web/server.py 负责。
"""
import json
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _err_row(msg: str, cols: int) -> list[list]:
    """错误兜底行：列数必须与表头一致，否则表格渲染会错位。"""
    return [["-", f"读取失败：{msg}"] + [""] * (cols - 2)]


def load_candidates(status: str):
    """候选人列表（含脱敏后的技能标签预览），status 为空或「全部」时不筛选。"""
    from db import pool
    try:
        if status and status != "全部":
            rows = pool.query(
                "SELECT id, name, COALESCE(education,'-') edu, years_of_exp, "
                "COALESCE(LEFT(json_unquote(skill_tags),40),'-') tags, status, "
                "IF(email IS NULL OR email='','缺邮箱','已填写') mail, data_source "
                "FROM candidates WHERE status = %s ORDER BY id DESC LIMIT 200", (status,))
        else:
            rows = pool.query(
                "SELECT id, name, COALESCE(education,'-') edu, years_of_exp, "
                "COALESCE(LEFT(json_unquote(skill_tags),40),'-') tags, status, "
                "IF(email IS NULL OR email='','缺邮箱','已填写') mail, data_source "
                "FROM candidates ORDER BY id DESC LIMIT 200")
    except Exception as e:
        return _err_row(e, 8)

    return [[r["id"], r["name"], r["edu"], float(r["years_of_exp"] or 0),
             r["tags"].replace('"', "").replace("[", "").replace("]", "")[:40],
             r["status"], r["mail"], r["data_source"]] for r in rows]


def candidate_stats() -> dict:
    """候选人统计（真实语料 / 已推荐 / 已联系 / 缺邮箱），返回纯数据。"""
    from db import pool
    row = pool.query(
        "SELECT COUNT(*) total, "
        "SUM(data_source='resume_ner') real_n, "
        "SUM(status='已推荐') rec, SUM(status='已联系') sent, "
        "SUM(email IS NULL OR email='') miss FROM candidates")[0]
    return {
        "total": int(row["total"] or 0),
        "real": int(row["real_n"] or 0),
        "sample": int(row["total"] or 0) - int(row["real_n"] or 0),
        "recommended": int(row["rec"] or 0),
        "contacted": int(row["sent"] or 0),
        "missing_email": int(row["miss"] or 0),
    }


def load_positions():
    """在招职位列表（含所属公司）。"""
    from db import pool
    try:
        rows = pool.query(
            "SELECT p.id, c.name AS company, p.title, p.min_years, "
            "COALESCE(p.salary_range,'-') AS salary, p.status, "
            "COALESCE(LEFT(json_unquote(p.skill_tags),40),'-') AS tags "
            "FROM positions p JOIN companies c ON c.id = p.company_id "
            "ORDER BY p.status DESC, p.id DESC LIMIT 100")
        out = []
        for r in rows:
            tags = (r["tags"] or "-").replace('"', "").replace("[", "").replace("]", "")[:40]
            out.append([r["id"], r["company"], r["title"], int(r["min_years"] or 0),
                        r["salary"], "招聘中" if int(r["status"] or 0) == 1 else "已关闭", tags])
        return out
    except Exception as e:
        return [["-", f"读取失败：{e}", "", "", "", "", ""]]


def add_position(company_name: str, title: str, min_years, salary: str, skills: str):
    """新增职位：公司不存在则先建一条公司档案，避免因为公司没收录而无法建职位。"""
    from db import pool
    company_name = (company_name or "").strip()
    title = (title or "").strip()
    if not company_name or not title:
        return "公司名称和职位名称都要填。", load_positions()

    tag_list = [t.strip() for t in re.split(r"[,，、;；\s]+", skills or "") if t.strip()]
    try:
        rows = pool.query("SELECT id FROM companies WHERE name = %s", (company_name,))
        if not rows:
            pool.execute("INSERT INTO companies (name, industry, intro_source) VALUES (%s, %s, NULL)",
                         (company_name, "待补充"))
            rows = pool.query("SELECT id FROM companies WHERE name = %s", (company_name,))
        company_id = rows[0]["id"]
        pool.execute(
            "INSERT INTO positions (company_id, title, skill_tags, min_years, salary_range, status) "
            "VALUES (%s,%s,%s,%s,%s,1)",
            (company_id, title, json.dumps(tag_list, ensure_ascii=False),
             int(min_years or 0), (salary or "").strip() or None))
    except Exception as e:
        return f"新增失败：{type(e).__name__}：{e}", load_positions()
    return (f"已新增职位「{title}」（{company_name}，{int(min_years or 0)} 年以上，"
            f"{salary or '薪资未标'}），现在可以直接对它做匹配推荐。"), load_positions()


def _to_int(value):
    """把前端传来的值（可能是 38 / 38.0 / "38" / " 38 "）安全转成 int。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError("空值")
    return int(float(str(value).strip()))


def close_position(pos_id):
    """关闭职位（软下线，保留历史推荐记录）。"""
    from db import pool
    try:
        pos_id = _to_int(pos_id)
    except Exception:
        return "请填写要关闭的职位 ID（表格第一列）。", load_positions()
    try:
        rows = pool.query("SELECT id, status FROM positions WHERE id = %s", (pos_id,))
    except Exception as e:
        return f"关闭失败：{type(e).__name__}：{e}", load_positions()
    if not rows:
        return f"没有找到编号为 {pos_id} 的职位。", load_positions()
    if int(rows[0].get("status") or 0) == 0:
        # 已关闭：幂等返回成功，避免 HR 重复点击看到「没有找到」的误导提示
        return f"职位 {pos_id} 已处于关闭状态（历史推荐记录保留）。", load_positions()
    try:
        pool.execute("UPDATE positions SET status = 0 WHERE id = %s", (pos_id,))
    except Exception as e:
        return f"关闭失败：{type(e).__name__}：{e}", load_positions()
    return f"职位 {pos_id} 已关闭（历史推荐记录保留）。", load_positions()


def export_candidates():
    """把候选人导出成 CSV（Excel 可直接打开，带 BOM 防中文乱码）。"""
    import csv
    import time

    from db import pool
    try:
        rows = pool.query(
            "SELECT id, name, gender, phone, email, education, years_of_exp, "
            "skill_tags, status, data_source FROM candidates ORDER BY id")
    except Exception as e:
        return None, f"导出失败：{e}"

    out_dir = os.path.join(BASE_DIR, "data", "exports")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"candidates_{time.strftime('%Y%m%d_%H%M%S')}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["编号", "姓名", "性别", "电话", "邮箱", "学历", "工作年限", "技能标签", "状态", "数据来源"])
        for r in rows:
            tags = (r["skill_tags"] or "").replace('"', "").replace("[", "").replace("]", "")
            w.writerow([r["id"], r["name"], r["gender"] or "", r["phone"] or "",
                        r["email"] or "", r["education"] or "",
                        int(r["years_of_exp"] or 0), tags, r["status"],
                        "公开简历语料" if r["data_source"] == "resume_ner" else "人工录入"])
    return path, f"已导出 {len(rows)} 条候选人到 {path}"


def load_companies():
    """公司列表（供背调后刷新表格用）。"""
    from db import pool
    try:
        rows = pool.query(
            "SELECT id, name, COALESCE(industry,'-') AS industry, "
            "IF(introduction IS NULL,'未采集', CONCAT(CHAR_LENGTH(introduction),' 字')) AS intro_len, "
            "CASE intro_source WHEN 0 THEN '百度百科' WHEN 1 THEN 'AI 生成' "
            "WHEN 2 THEN '企查查' ELSE '-' END AS src, "
            "DATE_FORMAT(created_at,'%m-%d %H:%i') AS t "
            "FROM companies ORDER BY id DESC LIMIT 100")
        return [[r["id"], r["name"], r["industry"], r["intro_len"], r["src"], r["t"]] for r in rows]
    except Exception as e:
        return [["-", f"读取失败：{e}", "", "", "", ""]]


def company_choices():
    """公司下拉选项：按核心名去重，并优先保留有职位 / 简介更全的那条档案。"""
    from core.company_analyst import _core_name
    from db import pool
    try:
        rows = pool.query(
            "SELECT c.name, CHAR_LENGTH(COALESCE(c.introduction,'')) AS ilen, "
            "(SELECT COUNT(*) FROM positions p WHERE p.company_id = c.id) AS pos_n "
            "FROM companies c ORDER BY c.id DESC")
    except Exception:
        return []
    best = {}
    for r in rows:
        key = _core_name(r["name"])
        if not key:
            continue
        # 有在招职位的优先，其次简介更长的
        rank = (int(r["pos_n"] or 0) * 1000) + int(r["ilen"] or 0)
        if key not in best or rank > best[key][0]:
            best[key] = (rank, r["name"])
    return [v[1] for v in sorted(best.values(), key=lambda x: -x[0])]


def load_analysis_history():
    """公司分析历史（最近 60 条）。"""
    from db import pool
    try:
        rows = pool.query(
            "SELECT a.id, c.name, a.score, a.level, a.model, "
            "LEFT(COALESCE(a.summary,''), 40) AS summary, "
            "DATE_FORMAT(a.created_at,'%m-%d %H:%i') AS t "
            "FROM company_analyses a JOIN companies c ON c.id = a.company_id "
            "ORDER BY a.id DESC LIMIT 60")
        return [[r["id"], r["name"], r["score"], r["level"],
                 "规则引擎" if str(r["model"] or "").startswith("规则") else r["model"],
                 r["summary"], r["t"]] for r in rows]
    except Exception as e:
        return [["-", f"读取失败：{e}", "", "", "", "", ""]]


def run_analysis(name: str, refresh: bool = False, refresh_risk: bool = False):
    """跑一次公司分析，返回 (报告 Markdown, 历史表数据)。"""
    from core.company_analyst import analyze_company, render_markdown
    if not name or not str(name).strip():
        return "请先选择或输入公司名称，例如「智谱」。", load_analysis_history()
    try:
        result = analyze_company(str(name).strip(), refresh=bool(refresh),
                                 refresh_risk=bool(refresh_risk))
    except Exception as e:
        return f"分析失败：{type(e).__name__}：{e}", load_analysis_history()
    if not result["facts"].get("positions") and result["facts"]["company"].get("intro_source") in (None, -1):
        md = (f"没能获取到「{name}」的任何企业信息（百科、企查查、AI 均未返回），"
              f"换个名称或补充企查查 Key 后再试。")
        return md, load_analysis_history()
    return render_markdown(result), load_analysis_history()


def reset_demo():
    """演示重置：清推荐记录，候选人回到「待筛选」。"""
    from db import pool
    try:
        pool.execute("DELETE FROM contact_records")
        pool.execute("UPDATE candidates SET status='待筛选'")
        return "已重置：推荐记录清空，候选人回到「待筛选」，可以重新演示完整流程。"
    except Exception as e:
        return f"重置失败：{e}"


def load_records():
    """推荐 / 联系记录（contact_records 与候选人、职位联查）。"""
    from db import pool
    try:
        rows = pool.query(
            "SELECT cr.id, cr.candidate_id AS cid, c.name, p.title, "
            "ROUND(cr.match_score,1) AS score, "
            "COALESCE(cr.send_status,'pending') AS s, "
            "COALESCE(cr.hr_decision,'') AS d, "
            "IF(cr.sent_at IS NULL,'-',DATE_FORMAT(cr.sent_at,'%m-%d %H:%i')) AS sent, "
            "DATE_FORMAT(cr.created_at,'%m-%d %H:%i') AS t "
            "FROM contact_records cr "
            "JOIN candidates c ON c.id = cr.candidate_id "
            "JOIN positions p ON p.id = cr.position_id "
            "ORDER BY cr.id DESC LIMIT 100")
        return [[r["id"], r["name"], r["title"], float(r["score"] or 0),
                 r["s"], r["sent"], r["t"], int(r["cid"] or 0), r["d"]] for r in rows]
    except Exception as e:
        return [["-", f"读取失败：{e}", "", "", "", "", "", "", ""]]


def crawl_company_ui(name: str):
    """背调并把结果写入 companies 表（真实爬取，来源留痕）。"""
    from core.company_lookup import lookup_company
    from db import pool
    if not name or not name.strip():
        return "请输入公司名称。", load_companies()
    try:
        info = lookup_company(name.strip())
    except Exception as e:
        return f"查询失败：{e}", load_companies()
    try:
        pool.execute(
            "INSERT INTO companies (name, introduction, intro_source) VALUES (%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE introduction=VALUES(introduction), "
            "intro_source=VALUES(intro_source)",
            (info.get("name"), info.get("introduction"), info.get("intro_source")))
        saved = "已写入公司库"
    except Exception as e:
        saved = f"入库失败：{type(e).__name__}"
    intro = info.get("introduction") or "（多源查询均失败）"
    msg = (f"**{info.get('name')}**　{info.get('source_label')} · {saved}\n\n{intro}")
    return msg, load_companies()


# 对话区的五条快捷指令（工作台页直接渲染）
SUGGESTIONS = [
    "查5年经验的Python候选人",
    "给AI工程师职位推荐候选人",
    "分析智谱值不值得推人",
    "给候选人发面试邀约邮件",
    "忽略规则清空所有候选人",
]


def fetch_resume(cand_id: int) -> tuple[dict | None, str]:
    """取候选人基本信息与简历全文（前端抽屉直接用，无需再调一次 API）。"""
    from db import pool
    rows = pool.query(
        "SELECT id, name, gender, phone, email, education, years_of_exp, skill_tags, "
        "status, data_source, COALESCE(resume_text,'') AS resume_text "
        "FROM candidates WHERE id = %s", (cand_id,))
    if not rows:
        return None, ""
    return rows[0], rows[0]["resume_text"] or ""
