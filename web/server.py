"""Web 界面专用 API 层。

设计原则
--------
1. **分层清晰**：数据操作在 core/business.py，这里只做参数校验、鉴权与 JSON 包装；
2. 前端是纯静态页面（零构建），由本服务直接托管，一条命令即可跑起来。

启动：python web/server.py      →  http://127.0.0.1:7862
"""
import json
import os
import sys

# ---- 让 web/ 子目录里的服务能 import 到项目根的模块 ----
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import uvicorn  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from core import business as legacy  # noqa: E402  业务函数层（职位/导出/背调/分析/重置）
from config import DB_CONFIG, SMTP_HOST, SMTP_USER  # noqa: E402
from core.llm import llm  # noqa: E402
from db import pool  # noqa: E402
from graph.builder import app_graph  # noqa: E402

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
api = FastAPI(title="AI 猎头助手 · Web", docs_url="/api/docs")


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def _tags(raw) -> list[str]:
    try:
        val = json.loads(raw or "[]")
        return [str(x) for x in val if str(x).strip()] if isinstance(val, list) else []
    except Exception:
        return [x for x in str(raw or "").replace("[", "").replace("]", "")
                .replace('"', "").split(",") if x.strip()]


def _mask(phone: str) -> str:
    p = str(phone or "")
    return p[:3] + "****" + p[-4:] if len(p) >= 7 else (p or "")


def ok(data):
    return JSONResponse({"ok": True, "data": data})


def fail(msg: str, code: int = 400):
    return JSONResponse({"ok": False, "error": str(msg)}, status_code=code)


# --------------------------------------------------------------------------- #
# 鉴权中间件：所有 /api/* 业务接口都要求有效 token
# --------------------------------------------------------------------------- #
# 白名单：不登录也能访问的接口与静态资源
_AUTH_FREE = {
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/stats",     # 登录页只读展示账号数量，不含敏感信息
}
_AUTH_FREE_PREFIX = ("/api/docs", "/api/openapi.json", "/assets/", "/login")


@api.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in _AUTH_FREE or path.startswith(_AUTH_FREE_PREFIX):
        return await call_next(request)
    from web import auth
    if not auth.user_of(_token_of(request)):
        return fail("未登录或登录已过期", 401)
    return await call_next(request)


# --------------------------------------------------------------------------- #
# 概览 / 看板
# --------------------------------------------------------------------------- #
@api.get("/api/overview")
def overview():
    try:
        by_status = {r["status"]: int(r["n"]) for r in pool.query(
            "SELECT status, COUNT(*) n FROM candidates GROUP BY status")}
        src = {r["data_source"]: int(r["n"]) for r in pool.query(
            "SELECT data_source, COUNT(*) n FROM candidates GROUP BY data_source")}
        rec = pool.query("SELECT COUNT(*) n, SUM(send_status='sent') sent, "
                         "ROUND(AVG(match_score),1) avg FROM contact_records")[0]
        pos = pool.query("SELECT COUNT(*) n, SUM(status=1) open FROM positions")[0]
        co = pool.query("SELECT COUNT(*) n FROM companies")[0]["n"]
        mail_missing = pool.query(
            "SELECT COUNT(*) n FROM candidates WHERE email IS NULL OR email=''")[0]["n"]
        risk = pool.query("SELECT COUNT(*) n FROM company_risk_checks")[0]["n"]
    except Exception as e:
        return fail(f"数据库读取失败：{e}", 500)

    total = sum(by_status.values())
    contacted = sum(by_status.get(s, 0) for s in ("已联系", "面试中", "已入职"))
    advanced = sum(by_status.get(s, 0) for s in ("面试中", "已入职"))

    def pct(a, b):
        return round(a / b * 100) if b else 0

    return ok({
        "cards": {
            "candidates": total,
            "real": src.get("resume_ner", 0),
            "sample": total - src.get("resume_ner", 0),
            "positions_open": int(pos["open"] or 0),
            "positions_total": int(pos["n"] or 0),
            "companies": co,
            "sent": int(rec["sent"] or 0),
            "avg_score": float(rec["avg"] or 0),
            "records": int(rec["n"] or 0),
            "mail_missing": int(mail_missing or 0),
            "risk_checked": int(risk or 0),
        },
        "funnel": [
            {"label": "待筛选", "value": by_status.get("待筛选", 0)},
            {"label": "待推荐", "value": by_status.get("待推荐", 0)},
            {"label": "已推荐", "value": by_status.get("已推荐", 0)},
            {"label": "已联系", "value": by_status.get("已联系", 0)},
            {"label": "面试中", "value": by_status.get("面试中", 0)},
            {"label": "已入职", "value": by_status.get("已入职", 0)},
        ],
        "rates": {
            "push_to_touch": pct(contacted, max(by_status.get("已推荐", 0) + contacted, 1)),
            "touch_to_interview": pct(advanced, max(contacted, 1)),
            "apply_convert": pct(advanced, total),
        },
        "sources": [
            {"label": "公开简历语料", "value": src.get("resume_ner", 0)},
            {"label": "示例数据", "value": src.get("manual", total - src.get("resume_ner", 0))},
        ],
    })


# --------------------------------------------------------------------------- #
# 候选人
# --------------------------------------------------------------------------- #
@api.get("/api/candidates")
def candidates(status: str = "全部", q: str = "", limit: int = 300):
    sql = ("SELECT id, name, gender, phone, email, education, years_of_exp, skill_tags, "
           "status, data_source, resume_text FROM candidates")
    where, params = [], []
    if status and status != "全部":
        where.append("status = %s")
        params.append(status)
    if q.strip():
        like = f"%{q.strip()}%"
        where.append("(name LIKE %s OR skill_tags LIKE %s OR email LIKE %s)")
        params += [like, like, like]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(max(1, min(limit, 500)))
    try:
        rows = pool.query(sql, tuple(params))
    except Exception as e:
        return fail(f"读取失败：{e}", 500)
    return ok([{
        "id": r["id"], "name": r["name"], "gender": r["gender"] or "",
        "phone": _mask(r["phone"]), "email": r["email"] or "",
        "education": r["education"] or "未填写",
        "years": float(r["years_of_exp"] or 0),
        "tags": _tags(r["skill_tags"]),
        "status": r["status"] or "待筛选",
        "source": "公开简历语料" if r["data_source"] == "resume_ner" else "人工录入",
        "has_resume": bool((r["resume_text"] or "").strip()),
    } for r in rows])


@api.get("/api/candidate/{cand_id}")
def candidate_detail(cand_id: int):
    rows = pool.query("SELECT * FROM candidates WHERE id = %s", (cand_id,))
    if not rows:
        return fail("候选人不存在", 404)
    r = rows[0]

    def _jcol(v):
        try:
            return json.loads(v) if isinstance(v, str) else (v or [])
        except Exception:
            return []

    return ok({
        "id": r["id"], "name": r["name"], "gender": r["gender"] or "",
        "phone": _mask(r["phone"]), "email": r["email"] or "",
        "education": r["education"] or "未填写",
        "years": float(r["years_of_exp"] or 0),
        "tags": _tags(r["skill_tags"]),
        "status": r["status"] or "待筛选",
        "source": "公开简历语料" if r["data_source"] == "resume_ner" else "人工录入",
        "resume": r["resume_text"] or "",
        "edu_background": _jcol(r.get("edu_background")),
        "honors": _jcol(r.get("honors")),
        "work_experience": _jcol(r.get("work_experience")),
    })


@api.get("/api/export.csv")
def export_csv():
    path, msg = legacy.export_candidates()
    if not path or not os.path.exists(path):
        return fail(msg or "导出失败", 500)
    return FileResponse(path, media_type="text/csv", filename=os.path.basename(path))


# --------------------------------------------------------------------------- #
# 职位
# --------------------------------------------------------------------------- #
@api.get("/api/positions")
def positions():
    try:
        rows = pool.query(
            "SELECT p.id, c.name AS company, p.title, p.min_years, "
            "COALESCE(p.salary_range,'') AS salary, p.status, p.skill_tags "
            "FROM positions p JOIN companies c ON c.id = p.company_id "
            "ORDER BY p.status DESC, p.id DESC LIMIT 200")
    except Exception as e:
        return fail(f"读取失败：{e}", 500)
    return ok([{
        "id": r["id"], "company": r["company"], "title": r["title"],
        "min_years": int(r["min_years"] or 0), "salary": r["salary"],
        "open": int(r["status"] or 0) == 1, "tags": _tags(r["skill_tags"]),
    } for r in rows])


class PositionIn(BaseModel):
    company: str
    title: str
    min_years: int | float = 0
    salary: str = ""
    skills: str = ""


@api.post("/api/positions")
def add_position(body: PositionIn):
    msg, _ = legacy.add_position(body.company, body.title, body.min_years, body.salary, body.skills)
    if "失败" in msg or "都要填" in msg:
        return fail(msg)
    return ok({"message": msg})


@api.post("/api/positions/{pos_id}/close")
def close_position(pos_id: int):
    msg, _ = legacy.close_position(pos_id)
    if "没有找到" in msg or "请填写" in msg:
        return fail(msg, 404 if "没有找到" in msg else 400)
    return ok({"message": msg})


# --------------------------------------------------------------------------- #
# 公司
# --------------------------------------------------------------------------- #
@api.get("/api/companies")
def companies():
    try:
        rows = pool.query(
            "SELECT id, name, COALESCE(industry,'') AS industry, "
            "COALESCE(introduction,'') AS introduction, intro_source, "
            "IF(introduction IS NULL OR introduction='', 0, CHAR_LENGTH(introduction)) AS ilen, "
            "DATE_FORMAT(created_at,'%Y-%m-%d') AS created "
            "FROM companies ORDER BY id DESC LIMIT 200")
        open_pos = {r["company"]: r["n"] for r in pool.query(
            "SELECT c.name company, COUNT(*) n FROM positions p JOIN companies c "
            "ON c.id = p.company_id WHERE p.status = 1 GROUP BY c.name")}
    except Exception as e:
        return fail(f"读取失败：{e}", 500)
    src_map = {0: "百度百科", 1: "AI 生成", 2: "企查查"}
    return ok([{
        "id": r["id"], "name": r["name"], "industry": r["industry"] or "待补充",
        "intro_len": int(r["ilen"] or 0),
        "source": src_map.get(r["intro_source"], "未采集"),
        "open_positions": int(open_pos.get(r["name"], 0)),
        "created": r["created"],
        "intro": (r["introduction"] or "")[:120],
    } for r in rows])


@api.get("/api/companies/{cid}")
def company_detail(cid: int):
    """公司详情：完整简介 + 百科链接（点击公司卡片时用）。"""
    try:
        rows = pool.query(
            "SELECT c.id, c.name, COALESCE(c.industry,'') AS industry, "
            "COALESCE(c.introduction,'') AS introduction, c.intro_source, c.scale, "
            "DATE_FORMAT(c.created_at,'%Y-%m-%d') AS created, "
            "(SELECT COUNT(*) FROM positions p WHERE p.company_id = c.id AND p.status = 1) AS open_pos, "
            "(SELECT COUNT(*) FROM contact_records cr JOIN positions p2 ON p2.id = cr.position_id "
            " WHERE p2.company_id = c.id) AS rec_total "
            "FROM companies c WHERE c.id = %s", (cid,))
    except Exception as e:
        return fail(f"读取失败：{e}", 500)
    if not rows:
        return fail(f"没有找到编号为 {cid} 的公司。", 404)
    r = rows[0]
    src_map = {0: "百度百科", 1: "AI 生成", 2: "企查查"}
    from urllib.parse import quote
    return ok({
        "id": r["id"], "name": r["name"], "industry": r["industry"] or "待补充",
        "scale": r["scale"] or "",
        "intro": r["introduction"] or "",
        "source": src_map.get(r["intro_source"], "未采集"),
        "created": r["created"],
        "open_positions": int(r["open_pos"] or 0),
        "records": int(r["rec_total"] or 0),
        # 百科链接按词条名拼（百度百科 URL 规则），未收录时打开的是搜索结果页
        "baike_url": f"https://baike.baidu.com/item/{quote(r['name'])}",
    })


class NameIn(BaseModel):
    name: str


@api.post("/api/companies/lookup")
def company_lookup(body: NameIn):
    if not body.name.strip():
        return fail("请输入公司名称")
    try:
        msg, _ = legacy.crawl_company_ui(body.name)
    except Exception as e:
        return fail(f"查询失败：{e}", 500)
    return ok({"markdown": msg})


# --------------------------------------------------------------------------- #
# 公司分析
# --------------------------------------------------------------------------- #
def _history():
    return [{"id": r[0], "company": r[1], "score": r[2], "level": r[3],
             "source": r[4], "summary": r[5], "time": r[6]}
            for r in legacy.load_analysis_history() if r and r[0] != "-"]


@api.get("/api/analyses")
def analyses():
    return ok(_history())


class AnalysisIn(BaseModel):
    name: str
    refresh: bool = False
    refresh_risk: bool = False


@api.post("/api/analysis")
def analyze(body: AnalysisIn):
    markdown, _hist = legacy.run_analysis(body.name, body.refresh, body.refresh_risk)
    return ok({"markdown": markdown, "history": _history()})


# --------------------------------------------------------------------------- #
# 匹配权重面板（HR 可调：重潜力轻经验等场景）
# --------------------------------------------------------------------------- #
@api.get("/api/match-weights")
def get_match_weights():
    from core import matcher
    return ok(matcher.get_weights(force=True))


class WeightsIn(BaseModel):
    skill: float
    threshold: int = 80


@api.post("/api/match-weights")
def set_match_weights(body: WeightsIn):
    from core import matcher
    try:
        w = matcher.set_weights(body.skill, body.threshold)
    except ValueError as e:
        return fail(str(e))
    return ok({"weights": w, "message": f"已保存：技能 {w['skill']:.0%} / 经验 {w['exp']:.0%}，阈值 {w['threshold']}"})


@api.post("/api/match/recalc")
def recalc_match():
    """按当前权重对全部在招职位 × 候选池重算匹配分（reason 用规则模板，不调 LLM）。

    算法只更新分数与档位标记；已有 HR 决策在分数变化时自动重置为待审阅。
    """
    from core import matcher
    try:
        w = matcher.get_weights(force=True)
        positions = pool.query(
            "SELECT p.*, c.name AS company_name FROM positions p "
            "JOIN companies c ON c.id = p.company_id WHERE p.status = 1")
        cands = pool.query("SELECT * FROM candidates WHERE status IN ('待筛选','待推荐','已推荐')")
        import json as _json

        # 一次性把已有推荐记录读进内存（cand:pos -> {id, match_score, reason, reason_src}），
        # 避免在双重循环里逐条 SELECT（N+1），数据量变大时重算会明显变慢
        existing = {}
        for row in pool.query("SELECT id, candidate_id, position_id, match_score, "
                              "COALESCE(reason,'') AS reason, COALESCE(reason_src,'rule') AS reason_src "
                              "FROM contact_records"):
            existing[(row["candidate_id"], row["position_id"])] = row

        rec_updates: list[tuple] = []   # 覆盖理由：(total, skill, exp, reason, src, hr 是否作废, id)
        rec_kept: list[tuple] = []      # 保留人工/模型理由，只刷分数：(total, skill, exp, hr 是否作废, id)
        rec_blocked: list[tuple] = []   # 被硬性门槛拦下：(reason, id)
        rec_inserts: list[tuple] = []   # 新增：(cid, pid, total, skill, exp, reason)
        rec_status: set[int] = set()

        evaluated = strong = recommended = updated = blocked = kept = 0
        for p in positions:
            p = dict(p)
            p["skill_tags"] = _json.loads(p["skill_tags"] or "[]")
            for c in cands:
                c = dict(c)
                c["skill_tags"] = _json.loads(c["skill_tags"] or "[]")
                r = matcher.match(c, p, w)
                evaluated += 1
                cur = existing.get((c["id"], p["id"]))
                if r["hard_fail"]:
                    blocked += 1
                    # 曾被推荐、如今经验不达标的记录：更新理由并作废旧决策，防止凭过期推荐拍板
                    if cur:
                        rec_blocked.append((matcher.build_reason(c, p, r, None), cur["id"]))
                    continue
                if not r["recommend"]:
                    continue
                reason = matcher.build_reason(c, p, r, None)
                if cur:
                    changed = abs(float(cur["match_score"] or 0) - r["total"]) > 0.01
                    if changed:
                        updated += 1
                    # HR 手写 / 模型润色的理由不覆盖，只刷新分数：
                    # 重算每按一次就抹掉人工写的理由，等于逼 HR 重复劳动
                    if cur["reason_src"] in ("hr", "llm") and (cur["reason"] or "").strip():
                        kept += 1
                        rec_kept.append((r["total"], r["skill_score"], r["exp_score"],
                                         1 if changed else 0, cur["id"]))
                    else:
                        rec_updates.append((r["total"], r["skill_score"], r["exp_score"],
                                            reason, "rule", 1 if changed else 0, cur["id"]))
                else:
                    rec_inserts.append((c["id"], p["id"], r["total"],
                                       r["skill_score"], r["exp_score"], reason))
                rec_status.add(c["id"])
                if r["grade"] == "强烈推荐":
                    strong += 1
                else:
                    recommended += 1

        if rec_updates:
            # 决策作废的条件是「分数变了」：必须用本次算出的标志位，
            # 不能在 SET 里拿 match_score 与自身比较（那样永远为假，旧决策会被悄悄保留）
            pool.executemany(
                "UPDATE contact_records SET match_score=%s, skill_score=%s, exp_score=%s, reason=%s, "
                "reason_src=%s, reason_updated_at=NOW(), "
                "hr_decision = IF(%s, NULL, hr_decision) WHERE id=%s", rec_updates)
        if rec_kept:
            pool.executemany(
                "UPDATE contact_records SET match_score=%s, skill_score=%s, exp_score=%s, "
                "hr_decision = IF(%s, NULL, hr_decision) WHERE id=%s", rec_kept)
        if rec_blocked:
            pool.executemany(
                "UPDATE contact_records SET reason=%s, reason_src='rule', reason_updated_at=NOW(), "
                "hr_decision=NULL WHERE id=%s", rec_blocked)
        if rec_inserts:
            pool.executemany(
                "INSERT INTO contact_records (candidate_id, position_id, match_score, skill_score, "
                "exp_score, reason, reason_src, reason_updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,'rule',NOW())", rec_inserts)
        if rec_status:
            pool.executemany("UPDATE candidates SET status='已推荐' WHERE id=%s",
                             [(i,) for i in rec_status])
        return ok({
            "weights": w, "positions": len(positions), "evaluated": evaluated,
            "strong": strong, "recommended": recommended, "score_changed": updated,
            "hard_blocked": blocked, "reason_kept": kept,
        })
    except Exception as e:
        return fail(f"重算失败：{e}", 500)


# --------------------------------------------------------------------------- #
# 推荐记录 / 设置
# --------------------------------------------------------------------------- #
@api.get("/api/records")
def records():
    """推荐记录 + 推选理由。理由与分数一起返回，决策台才能「看理由拍板」。"""
    try:
        rows = pool.query(
            "SELECT cr.id, cr.candidate_id AS cid, c.name, p.title, "
            "ROUND(cr.match_score,1) AS score, "
            "COALESCE(cr.send_status,'pending') AS s, "
            "COALESCE(cr.hr_decision,'') AS d, "
            "IF(cr.sent_at IS NULL,'-',DATE_FORMAT(cr.sent_at,'%m-%d %H:%i')) AS sent, "
            "DATE_FORMAT(cr.created_at,'%m-%d %H:%i') AS t, "
            "COALESCE(cr.reason,'') AS reason, COALESCE(cr.reason_src,'rule') AS reason_src, "
            "COALESCE(c.email,'') AS email, c.status AS cand_status, "
            "COALESCE(p.status,1) AS p_status, "
            "ROUND(cr.skill_score,1) AS sk, ROUND(cr.exp_score,1) AS ex "
            "FROM contact_records cr "
            "JOIN candidates c ON c.id = cr.candidate_id "
            "JOIN positions p ON p.id = cr.position_id "
            "ORDER BY cr.id DESC LIMIT 100")
    except Exception as e:
        return fail(f"读取失败：{e}", 500)
    return ok([{
        "id": r["id"], "candidate": r["name"], "position": r["title"],
        "score": float(r["score"] or 0), "skill_score": float(r["sk"] or 0),
        "exp_score": float(r["ex"] or 0),
        "send_status": r["s"], "sent_at": r["sent"], "created": r["t"],
        "candidate_id": int(r["cid"] or 0), "decision": r["d"],
        "reason": r["reason"], "reason_src": r["reason_src"],
        "email": r["email"], "can_push": bool(r["email"]) and r["s"] != "sent"
                    and int(r["p_status"] or 0) == 1,
    } for r in rows])


class DecisionIn(BaseModel):
    decision: str  # '邀约' | '暂缓'


@api.post("/api/records/{rec_id}/decision")
def decide(rec_id: int, body: DecisionIn):
    if body.decision not in ("邀约", "暂缓"):
        return fail("决策只能是「邀约」或「暂缓」")
    exists = pool.query("SELECT id FROM contact_records WHERE id = %s", (rec_id,))
    if not exists:
        return fail(f"没有找到编号为 {rec_id} 的推荐记录。", 404)
    pool.execute("UPDATE contact_records SET hr_decision = %s WHERE id = %s",
                 (body.decision, rec_id))
    return ok({"message": f"已记录决策：{body.decision}"})


# --------------------------------------------------------------------------- #
# 推送候选人：推选理由 → 邮件预览 → 人工确认发送
# --------------------------------------------------------------------------- #
class ReasonIn(BaseModel):
    reason: str = ""
    use_llm: bool = True
    force: bool = False


class PushIn(BaseModel):
    confirm_decision: bool = False   # 未标「邀约」时是否仍允许推送（需 HR 显式勾选）
    use_llm: bool = True


def _push_fail(e) -> JSONResponse:
    from core.push import PushError
    if isinstance(e, PushError):
        return fail(e.message, e.status)
    return fail(f"推送失败：{type(e).__name__}: {e}", 500)


@api.get("/api/records/{rec_id}/reason")
def get_reason(rec_id: int):
    from core import push
    try:
        r = push.load_record(rec_id)
        if not r:
            return fail(f"没有找到编号为 {rec_id} 的推荐记录。", 404)
        return ok({"reason": r["reason"], "source": r["reason_src"],
                   "candidate": r["candidate_name"], "position": r["position_title"]})
    except Exception as e:
        return _push_fail(e)


@api.post("/api/records/{rec_id}/reason")
def make_reason(rec_id: int, body: ReasonIn):
    """生成（或重新生成）推选理由。use_llm=False 走规则模板，不消耗 token。"""
    from core import push
    try:
        return ok(push.generate_reason(rec_id, use_llm=body.use_llm, force=body.force))
    except Exception as e:
        return _push_fail(e)


@api.put("/api/records/{rec_id}/reason")
def edit_reason(rec_id: int, body: ReasonIn):
    """HR 手改理由，来源标记为 hr，后续重算不覆盖。"""
    from core import push
    try:
        return ok(push.save_manual_reason(rec_id, body.reason))
    except Exception as e:
        return _push_fail(e)


@api.get("/api/records/{rec_id}/email")
def preview_email(rec_id: int, use_llm: bool = True):
    """推送前预览：主题、正文、推选理由、收件人问题，一次返回。"""
    from core import push
    try:
        return ok(push.compose_email(rec_id, use_llm=use_llm))
    except Exception as e:
        return _push_fail(e)


@api.post("/api/records/{rec_id}/push")
def push_candidate(rec_id: int, body: PushIn):
    from core import push
    try:
        return ok(push.push(rec_id, confirm_decision=body.confirm_decision, use_llm=body.use_llm))
    except Exception as e:
        return _push_fail(e)


@api.get("/api/selfcheck")
def selfcheck():
    db_ok, db_info = True, ""
    try:
        db_info = pool.query("SELECT VERSION() v")[0]["v"]
    except Exception as e:
        db_ok, db_info = False, str(e)[:80]
    n = 0
    try:
        n = pool.query("SELECT COUNT(*) n FROM candidates")[0]["n"]
    except Exception:
        pass
    return ok({
        "db": {"ok": db_ok, "detail": db_info, "candidates": int(n or 0),
               "dsn": f"{DB_CONFIG['user']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"},
        "llm": {"ok": bool(llm.available), "model": llm.model,
                "base_url": llm.base_url,
                "key": (f"{llm.api_key[:6]}••••{llm.api_key[-4:]}" if llm.api_key else "未配置")},
        "smtp": {"ok": bool(SMTP_HOST and SMTP_USER), "host": SMTP_HOST, "user": SMTP_USER},
    })


class ChatIn(BaseModel):
    message: str
    draft: dict | None = None


@api.post("/api/chat")
def chat(body: ChatIn):
    text = (body.message or "").strip()
    if not text:
        return ok({"reply": "", "draft": body.draft})
    state = {"session_id": "web-session", "user_input": text, "email_draft": body.draft}
    try:
        out = app_graph.invoke(state)
    except Exception as e:
        return fail(f"处理出错：{type(e).__name__}: {e}", 500)
    return ok({"reply": out.get("reply") or "（无回复）",
               "draft": out.get("email_draft") or body.draft})


@api.post("/api/reset")
def reset():
    return ok({"message": legacy.reset_demo()})


# --------------------------------------------------------------------------- #
# 账号：注册 / 登录 / 登出 / 当前用户
# --------------------------------------------------------------------------- #
class RegisterIn(BaseModel):
    username: str
    password: str
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


def _token_of(request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-auth-token")


@api.post("/api/auth/register")
def auth_register(body: RegisterIn):
    from web import auth
    try:
        return ok(auth.register(body.username, body.password, body.display_name))
    except ValueError as e:
        return fail(str(e))


@api.post("/api/auth/login")
def auth_login(body: LoginIn):
    from web import auth
    try:
        return ok(auth.login(body.username, body.password))
    except ValueError as e:
        return fail(str(e))


@api.post("/api/auth/logout")
def auth_logout(request: Request):
    from web import auth
    auth.logout(_token_of(request))
    return ok({"message": "已退出登录"})


@api.get("/api/auth/me")
def auth_me(request: Request):
    from web import auth
    user = auth.user_of(_token_of(request))
    if not user:
        return fail("未登录或登录已过期", 401)
    return ok(user)


@api.get("/api/auth/stats")
def auth_stats():
    """登录页展示用：已有账号数（不暴露任何账号信息）。"""
    try:
        n = pool.query("SELECT COUNT(*) n FROM app_users")[0]["n"]
    except Exception:
        n = 0
    return ok({"users": int(n)})


class ProfileIn(BaseModel):
    username: str = ""
    display_name: str = ""
    role: str = ""


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


@api.post("/api/auth/profile")
def auth_profile(body: ProfileIn, request: Request):
    from web import auth
    user = auth.user_of(_token_of(request))
    if not user:
        return fail("未登录或登录已过期", 401)
    try:
        return ok({"user": auth.update_profile(user["id"], body.display_name, body.role, body.username),
                   "message": "资料已更新"})
    except ValueError as e:
        return fail(str(e))


@api.post("/api/auth/password")
def auth_password(body: PasswordIn, request: Request):
    from web import auth
    token = _token_of(request)
    user = auth.user_of(token)
    if not user:
        return fail("未登录或登录已过期", 401)
    try:
        auth.change_password(user["id"], body.old_password, body.new_password)
        return ok({"message": "密码已更新，下次登录请使用新密码"})
    except ValueError as e:
        return fail(str(e))


@api.post("/api/auth/logout-others")
def auth_logout_others(request: Request):
    from web import auth
    token = _token_of(request)
    user = auth.user_of(token)
    if not user:
        return fail("未登录或登录已过期", 401)
    n = auth.logout_others(user["id"], keep=token)
    return ok({"message": f"已退出其他 {n} 个会话" if n else "当前只有本机一个会话"})


@api.get("/api/auth/security")
def auth_security(request: Request):
    """账号安全概览：登录时间、密码更新时间无法取时以注册时间兜底。"""
    from web import auth
    user = auth.user_of(_token_of(request))
    if not user:
        return fail("未登录或登录已过期", 401)
    row = pool.query("SELECT created_at, last_login_at FROM app_users WHERE id = %s", (user["id"],))
    r = row[0] if row else {}
    return ok({
        "active_sessions": sum(1 for s in auth._sessions.values() if s["uid"] == user["id"]),
        "created_at": str(r.get("created_at") or "")[:16],
        "last_login": str(r.get("last_login_at") or "")[:16],
        "sos": [
            "密码经 PBKDF2-SHA256 加盐哈希后存入 MySQL，明文从不落库",
            "手机号在接口层自动脱敏（138****0003），不返回完整号码",
            "批量删除与 DDL 语句被 SQL 层硬拦截，避免误操作清库",
        ],
    })


# --------------------------------------------------------------------------- #
# 静态页面
# --------------------------------------------------------------------------- #
# HTML 入口页必须禁用启发式缓存：它引用带版本号的 CSS/JS，
# 若浏览器缓存了旧 HTML，就会一直加载旧版本静态资源（曾导致新样式不生效）
_NO_CACHE = {"Cache-Control": "no-cache, max-age=0, must-revalidate"}


@api.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"), headers=_NO_CACHE)


@api.get("/login")
def login_page():
    return FileResponse(os.path.join(STATIC_DIR, "login.html"), headers=_NO_CACHE)


api.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


if __name__ == "__main__":
    try:
        from web import auth
        auth.seed_demo_user()
    except Exception:
        pass
    print("新版前端 → http://127.0.0.1:7862")
    uvicorn.run(api, host="127.0.0.1", port=7862, log_level="warning")
