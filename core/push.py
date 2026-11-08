"""候选人推送编排：推选理由生成 → 邮件草稿 → 人工确认 → 发送 → 状态落库。

为什么单独一层
--------------
`graph/nodes/email_node.py` 是对话式入口（HR 在聊天框说「确认发送」），
它把「拿最新一条 pending 记录」写死在节点里。Web 决策台需要的是
「HR 在表格某一行点推送」——指定记录、指定收件人、先看理由再发。
两者共享同一套 SMTP / 限额 / 收件人校验逻辑，因此抽到这里，
避免两条链路各写一份、日后其中一条悄悄绕过了限额或域名校验。

设计原则
--------
1. 理由一律**先落库再发信**，邮件里署的是当时存档的理由，不是临时再生成一份；
2. 未邀请（hr_decision 非「邀约」）的记录不允许推送，除非 HR 显式带上确认标记；
3. 示例/保留域名、每日限额、已发送去重，三道闸门全部保留；
4. 任何失败都不吞掉：返回结构里带 code，前端能区分「能修」和「不能修」。
"""
import json
import re

from config import DAILY_SEND_LIMIT
from core import matcher
from core.llm import llm
from core.mailer import draft_email, send_with_retry
from db import pool

# 与 email_node 保持一致的保留域名清单（示例邮箱真实发送必然退信）
RESERVED_DOMAINS = ("example.com", "example.org", "example.net", "test.com", "localhost", "test.local")


# --------------------------------------------------------------------------- #
# 推选理由
# --------------------------------------------------------------------------- #
_RECORD_SQL = (
    "SELECT cr.id, cr.candidate_id, cr.position_id, cr.match_score, cr.skill_score, "
    "cr.exp_score, COALESCE(cr.reason,'') AS reason, COALESCE(cr.reason_src,'rule') AS reason_src, "
    "COALESCE(cr.send_status,'pending') AS send_status, cr.sent_at, "
    "COALESCE(cr.email_subject,'') AS email_subject, COALESCE(cr.email_body,'') AS email_body, "
    "COALESCE(cr.hr_decision,'') AS hr_decision, "
    "c.name AS candidate_name, c.email, c.education, c.years_of_exp, c.skill_tags AS c_skills, "
    "c.status AS candidate_status, c.resume_text, "
    "p.title AS position_title, p.skill_tags AS p_skills, p.min_years, COALESCE(p.status,1) AS p_status, "
    "cp.name AS company_name "
    "FROM contact_records cr "
    "JOIN candidates c ON c.id = cr.candidate_id "
    "JOIN positions p ON p.id = cr.position_id "
    "JOIN companies cp ON cp.id = p.company_id "
    "WHERE cr.id = %s"
)


class PushError(Exception):
    """业务拦截（收件人不合法、未邀约、重复发送等），带机器可读的 code。"""

    def __init__(self, message: str, code: str = "blocked", status: int = 400):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status


def _jlist(raw) -> list[str]:
    try:
        val = json.loads(raw or "[]")
        return [str(x) for x in val if str(x).strip()] if isinstance(val, list) else []
    except Exception:
        return [x for x in str(raw or "").replace("[", "").replace("]", "").replace('"', "").split(",") if x.strip()]


def load_record(rec_id: int) -> dict | None:
    rows = pool.query(_RECORD_SQL, (rec_id,))
    if not rows:
        return None
    r = dict(rows[0])
    r["c_skills"] = _jlist(r.get("c_skills"))
    r["p_skills"] = _jlist(r.get("p_skills"))
    return r


def _candidate(r: dict) -> dict:
    return {"name": r["candidate_name"], "skill_tags": r["c_skills"],
            "years_of_exp": float(r.get("years_of_exp") or 0),
            "education": r.get("education") or ""}


def _position(r: dict) -> dict:
    return {"title": r["position_title"], "skill_tags": r["p_skills"],
            "min_years": int(r.get("min_years") or 0)}


def stored_match_result(r: dict) -> dict:
    """用落库的分数还原一个 result 字典，供理由渲染使用（避免重复调算法）。"""
    w = matcher.get_weights()
    total = float(r.get("match_score") or 0)
    years = float(r.get("years_of_exp") or 0)
    min_y = int(r.get("min_years") or 0)
    return {
        "total": total,
        "skill_score": float(r.get("skill_score") or 0),
        "exp_score": float(r.get("exp_score") or 0),
        "recommend": total >= w["threshold"],
        "passed": not (min_y > 0 and years < min_y),
        "hard_fail": min_y > 0 and years < min_y,
        "min_years": min_y,
        "years_of_exp": years,
        "grade": "不通过" if (min_y > 0 and years < min_y) else matcher.grade(total, w["threshold"]),
        "weights": dict(w),
        "hit_skills": sorted({matcher.normalize_skill(t) for t in r["c_skills"]}
                             & {matcher.normalize_skill(t) for t in r["p_skills"]}),
    }


def generate_reason(rec_id: int, use_llm: bool = True, force: bool = False) -> dict:
    """生成并保存推选理由。已有 LLM 理由时默认不重复生成（省 token）。"""
    r = load_record(rec_id)
    if not r:
        raise PushError(f"没有找到编号为 {rec_id} 的推荐记录。", "not_found", 404)

    cand, pos = _candidate(r), _position(r)
    result = stored_match_result(r)
    if result["hard_fail"]:
        # 硬性门槛不通过的记录不该存在推荐理由，直接写拦截说明
        text = matcher.build_reason(cand, pos, result, None)
        _save_reason(rec_id, text, "rule")
        return {"reason": text, "source": "rule", "blocked": True}

    has_llm_reason = r["reason"] and r["reason_src"] == "llm"
    if has_llm_reason and not force:
        return {"reason": r["reason"], "source": "llm", "cached": True}

    text, source = None, "rule"
    if use_llm and llm.available:
        text = matcher.sanitize_reason(llm.chat(matcher.reason_prompt(cand, pos, result)))
        if text:
            source = "llm"
    if not text:
        text = matcher.build_reason(cand, pos, result, None)
    _save_reason(rec_id, text, source)
    return {"reason": text, "source": source, "cached": False}


def _save_reason(rec_id: int, text: str, source: str) -> None:
    pool.execute(
        "UPDATE contact_records SET reason=%s, reason_src=%s, reason_updated_at=NOW() WHERE id=%s",
        (text, source, rec_id))


def save_manual_reason(rec_id: int, text: str) -> dict:
    """HR 手改理由：来源标记为 hr，重算不会覆盖（见 recalc 里的保留逻辑）。"""
    clean = (text or "").strip()
    if not clean:
        raise PushError("理由不能为空。", "invalid")
    if len(clean) > 600:
        raise PushError("理由最多 600 字。", "invalid")
    if not load_record(rec_id):
        raise PushError(f"没有找到编号为 {rec_id} 的推荐记录。", "not_found", 404)
    _save_reason(rec_id, clean, "hr")
    return {"reason": clean, "source": "hr"}


# --------------------------------------------------------------------------- #
# 推送
# --------------------------------------------------------------------------- #
def recipient_problem(addr: str) -> str | None:
    if not addr or "@" not in addr:
        return "收件人邮箱格式不正确"
    domain = addr.split("@")[1].lower()
    if domain in RESERVED_DOMAINS or domain.endswith((".test", ".invalid", ".example")):
        return f"收件人域名 {domain} 是示例/保留域名，真实环境不会送达"
    return None


def sent_today() -> int:
    try:
        return int(pool.query(
            "SELECT COUNT(*) n FROM contact_records "
            "WHERE send_status = 'sent' AND DATE(sent_at) = CURDATE()")[0]["n"] or 0)
    except Exception:
        return 0


def compose_email(rec_id: int, use_llm: bool = True) -> dict:
    """生成邮件草稿（含推选理由），不发送。前端用它做「推送前预览」。

    性能取舍：理由和邮件正文各调一次 LLM 要 30-40 秒，弹窗卡得没法用。
    这里一次调用同时产出「理由 + 正文」，用固定分隔符切分；切不开就退回
    规则模板，宁可次优也不能让 HR 干等。
    """
    r = load_record(rec_id)
    if not r:
        raise PushError(f"没有找到编号为 {rec_id} 的推荐记录。", "not_found", 404)

    cand, pos = _candidate(r), _position(r)
    company = {"name": r["company_name"]}
    result = stored_match_result(r)

    reason, reason_src, llm_text = None, "rule", None
    cached_reason = (r["reason"] or "").strip()
    if cached_reason and r["reason_src"] in ("llm", "hr"):
        reason, reason_src = cached_reason, r["reason_src"]

    if use_llm and llm.available and not result["hard_fail"]:
        reason, llm_text = _llm_reason_and_mail(cand, pos, company, result, reason)
        if reason:
            reason_src = "llm" if r["reason_src"] != "hr" else "hr"

    if not reason:
        reason = matcher.build_reason(cand, pos, result, None)
        if not (cached_reason and r["reason_src"] == "hr"):
            reason_src = "rule"
    if not (cached_reason and r["reason_src"] == "hr"):
        _save_reason(rec_id, reason, reason_src)

    draft = draft_email(cand, {**pos, "skill_tags": pos["skill_tags"]}, company, llm_text)
    body = draft["body"]
    # 规则兜底（没有 LLM 正文）时把推选依据附在末尾，保证有据可依
    if not llm_text:
        body = f"{body}\n\n—— 推选依据 ——\n{reason}"
    problem = recipient_problem(r.get("email") or "")
    return {
        "record_id": rec_id, "to": r.get("email") or "",
        "subject": draft["subject"], "body": body, "reason": reason,
        "reason_source": reason_src, "recipient_problem": problem,
        "send_status": r["send_status"], "sent_at": str(r["sent_at"] or ""),
        "candidate": r["candidate_name"], "position": r["position_title"],
        "company": r["company_name"], "score": float(r["match_score"] or 0),
    }


_SPLIT = "===邮件正文==="


def _llm_reason_and_mail(cand: dict, pos: dict, company: dict, result: dict,
                         known_reason: str | None) -> tuple[str | None, str | None]:
    """一次 LLM 调用同时产出推选理由与邀约正文，返回 (reason, mail_text)。"""
    f = matcher.reason_facts(cand, pos, result)
    need_reason = not known_reason
    parts = []
    if need_reason:
        parts.append(
            "【任务一：推选理由】\n"
            "写一段给用人部门看的内部推选理由（120-180 字，2-3 句）：第一句点明为什么推，"
            "第二句说清岗位要求与他/她的对应关系；有未覆盖技能要用一句说明这是面试重点确认的点；"
            "结尾提醒「算法结论仅供参考，请结合简历面谈确认」。"
            "只能用下面给出的标签、年限、学历作依据，不得编造项目经历、公司名、业绩数字。"
        )
    parts.append(
        "【任务二：面试邀约邮件】\n"
        f"写一封发给候选人本人的中文邀约邮件（150 字以内，含称呼、邀约意图、请回复确认时间、落款「{company.get('name')} 招聘团队」）。"
        "这是给候选人看的对外邮件，因此**绝不能出现任何内部评估信息**：不写算法分数、档位、权重、"
        "技能分/经验分、内部推选理由，也不要在末尾加括号注释说明你写了多少字或用了什么依据。"
    )
    prompt = (
        f"职位：{f['position']}（要求 ≥ {f['min_years']} 年经验，技能要求："
        f"{'、'.join(pos.get('skill_tags') or []) or '未填写'}）\n"
        f"候选人：{f['candidate']}（{f['years']:g} 年经验，{f['education'] or '学历未填写'}，"
        f"技能标签：{'、'.join(cand.get('skill_tags') or []) or '未填写'}）\n"
        f"算法明细（仅供你写内部理由，不得出现在邮件里）：命中 {'、'.join(f['hit_skills']) or '无'}；"
        f"未覆盖 {'、'.join(f['missing_skills']) or '无'}；总分 {f['total']}，档位 {f['grade']}\n"
    )
    if not need_reason:
        prompt += f"（内部推选理由已定稿，你只需写邮件：{known_reason[:200]}…）\n"
    prompt += ("\n" + "\n\n".join(parts) +
               f"\n\n输出格式严格如下（两段之间用一行 {_SPLIT} 分隔，不要加别的标题、编号或 Markdown）：\n")
    if need_reason:
        prompt += f"<推选理由正文>\n{_SPLIT}\n<邮件正文>"
    else:
        prompt += "<邮件正文>"

    out = matcher.sanitize_reason(llm.chat(prompt, temperature=0.5))
    if not out:
        return known_reason, None
    if _SPLIT in out:
        head, _, tail = out.partition(_SPLIT)
        reason = known_reason
        if need_reason:
            head = re.sub(r"^[\s*#]*推选理由[^\n]{0,6}", "", head).strip()
            reason = matcher.sanitize_reason(head) or known_reason
        mail = tail.strip()
        return reason, (mail or None)
    # 没按格式输出：整段当邮件，理由保持已知的（或退回模板）
    return known_reason, out



def push(rec_id: int, confirm_decision: bool = False, use_llm: bool = True) -> dict:
    """真正发送。返回 {status, message, to, record_id, reason, ...}。"""
    r = load_record(rec_id)
    if not r:
        raise PushError(f"没有找到编号为 {rec_id} 的推荐记录。", "not_found", 404)

    if r["send_status"] == "sent":
        raise PushError(
            f"该候选人已于 {r['sent_at']} 推送成功，无需重复发送。", "already_sent", 409)

    if int(r.get("p_status") or 0) != 1:
        raise PushError(f"职位「{r['position_title']}」已关闭，不再推送候选人。", "position_closed", 409)

    if r["hr_decision"] != "邀约" and not confirm_decision:
        raise PushError(
            "这条推荐还没有被标记为「邀约」。请先在决策台点「邀约」，或勾选「跳过决策直接推送」。",
            "need_decision", 409)

    problem = recipient_problem(r.get("email") or "")
    if problem:
        raise PushError(f"{problem}（当前：{r.get('email') or '空'}），请先换成真实邮箱。",
                        "bad_recipient", 400)

    if DAILY_SEND_LIMIT > 0:
        n = sent_today()
        if n >= DAILY_SEND_LIMIT:
            raise PushError(f"今天已发出 {n} 封，达到每日上限 {DAILY_SEND_LIMIT} 封。",
                            "quota", 429)

    draft = compose_email(rec_id, use_llm=use_llm)
    ok_sent = send_with_retry(draft["to"], draft["subject"], draft["body"])
    status = "sent" if ok_sent else "failed"
    pool.execute(
        "UPDATE contact_records SET send_status=%s, email_subject=%s, email_body=%s, "
        "sent_at=IF(%s='sent', NOW(), NULL) WHERE id=%s",
        (status, draft["subject"], draft["body"], status, rec_id))
    if ok_sent:
        pool.execute("UPDATE candidates SET status='已联系' WHERE id=%s", (r["candidate_id"],))
        return {"status": "sent", "to": draft["to"], "record_id": rec_id,
                "reason": draft["reason"], "reason_source": draft["reason_source"],
                "subject": draft["subject"],
                "message": f"已推送至 {draft['to']}，候选人状态更新为「已联系」。"}
    return {"status": "failed", "to": draft["to"], "record_id": rec_id,
            "reason": draft["reason"], "reason_source": draft["reason_source"],
            "subject": draft["subject"],
            "message": "发送失败（已重试 3 次），状态已标记为 failed，请检查 SMTP 配置。"}
