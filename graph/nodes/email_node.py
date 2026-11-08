"""公司背调节点 + 邮件触达节点（发送需用户显式确认）。"""
import json
import re

from config import DAILY_SEND_LIMIT
from core.company_lookup import lookup_company
from core.llm import llm
from core.mailer import draft_email, send_with_retry
from db import pool
from graph.state import GraphState


def company_node(state: GraphState) -> GraphState:
    name = _extract_company_name(state["user_input"])
    info = lookup_company(name)
    intro = info["introduction"] or "（多源查询均失败）"
    return {**state, "company_info": info,
            "reply": f"【{name}】{info['source_label']}\n\n{intro}"}


def _extract_company_name(text: str) -> str:
    """公司名抽取：优先匹配库中已知公司（长名优先），其次正则 + 口语清洗。

    处理"智谱这家公司怎么样"这类表达：去"这家/那家"，短品牌名去掉"公司"后缀
    （"智谱公司"->"智谱"，百科命中率更高），长全称则保留（"XX科技有限公司"）。
    """
    # 1. 库中已有公司名直接命中
    known = pool.query("SELECT name FROM companies")
    for row in sorted(known, key=lambda r: -len(r["name"])):
        if row["name"] and row["name"] in text:
            return row["name"]

    # 2. 引号包裹或"XX公司"形态
    candidate = text.strip()
    for pat in (r"「(.+?)」", r"\"(.+?)\"", r"([\u4e00-\u9fa5A-Za-z0-9]+公司)"):
        m = re.search(pat, text)
        if m:
            candidate = m.group(1)
            break

    # 3. 口语清洗
    candidate = re.sub(r"(这家|那家|的)", "", candidate).strip()
    if candidate.endswith("公司") and 2 <= len(candidate) - 2 <= 6:
        candidate = candidate[:-2]
    return candidate


_RESERVED_DOMAINS = ("example.com", "example.org", "example.net", "test.com", "localhost", "test.local")


def _recipient_problem(addr: str) -> str | None:
    """收件人合法性检查：拦住示例域名，避免真实发信退信并污染发送状态。"""
    if not addr or "@" not in addr:
        return "收件人邮箱格式不正确"
    domain = addr.split("@")[1].lower()
    if domain in _RESERVED_DOMAINS or domain.endswith((".test", ".invalid", ".example")):
        return f"收件人域名 {domain} 是示例/保留域名，真实环境不会送达"
    return None


def _sent_today() -> int:
    """今天已成功发出的邮件数（用于每日发送限额）。"""
    try:
        return int(pool.query(
            "SELECT COUNT(*) n FROM contact_records "
            "WHERE send_status = 'sent' AND DATE(sent_at) = CURDATE()")[0]["n"] or 0)
    except Exception:
        return 0


def email_node(state: GraphState) -> GraphState:
    """两条路径：未带确认 -> 生成草稿等待确认；带「确认发送」-> 实际发送。"""
    confirmed = "确认发送" in state["user_input"]

    if not confirmed:
        # 取最新的、且确实有邮箱的待发送记录（真实语料数据常缺邮箱，需自动跳过）
        rows = pool.query(
            "SELECT cr.id, c.name, c.email, p.title, p.skill_tags, p.company_id, cp.name AS company_name, "
            "COALESCE(cr.reason,'') AS reason "
            "FROM contact_records cr JOIN candidates c ON c.id = cr.candidate_id "
            "JOIN positions p ON p.id = cr.position_id "
            "JOIN companies cp ON cp.id = p.company_id "
            "WHERE cr.send_status = 'pending' AND c.email IS NOT NULL AND c.email <> '' "
            "ORDER BY cr.id DESC LIMIT 1"
        )
        if not rows:
            missing = pool.query(
                "SELECT COUNT(*) n FROM contact_records cr JOIN candidates c ON c.id = cr.candidate_id "
                "WHERE cr.send_status = 'pending' AND (c.email IS NULL OR c.email = '')")[0]["n"]
            hint = f"（其中 {missing} 条待发送记录的候选人缺少邮箱，已跳过）" if missing else ""
            return {**state, "reply": f"当前没有可发送的推荐记录，请先执行匹配，或为候选人补充邮箱。{hint}"}
        r = rows[0]
        company = {"name": r["company_name"]}
        # 把已落库的推选理由交给 LLM 当素材，邮件正文才能讲清「为什么找他」
        prompt = (f"写一封150字以内的中文面试邀约邮件。候选人:{r['name']}，职位:{r['title']}，"
                  f"公司:{r['company_name']}。")
        if r["reason"]:
            prompt += f"下面是我们内部记录的技术匹配理由，可作为邀约依据（不要照抄）：{r['reason']}"
        draft = draft_email(
            {"name": r["name"]}, {"title": r["title"], "skill_tags": json.loads(r["skill_tags"] or "[]")},
            company, llm.chat(prompt),
        )
        return {**state, "email_draft": {**draft, "record_id": r["id"], "to": r["email"],
                                         "reason": r["reason"]},
                "reply": f"邮件草稿已生成，收件人：{r['email']}\n主题：{draft['subject']}\n\n{draft['body']}\n\n"
                         f"回复「确认发送」以发送。"}

    # 确认发送
    draft = state.get("email_draft")
    if not draft:
        return {**state, "reply": "没有待确认的邮件草稿，请先生成。"}

    # 防重复发送：该记录已成功发送过则直接返回
    rec = pool.query("SELECT send_status, sent_at FROM contact_records WHERE id = %s", (draft["record_id"],))
    if rec and rec[0]["send_status"] == "sent":
        return {**state, "reply": f"该邮件已于 {rec[0]['sent_at']} 发送成功，无需重复发送。"}

    # 收件人校验：示例/保留域名直接拦下，不发送、不写 sent 状态
    problem = _recipient_problem(draft["to"])
    if problem:
        return {**state, "reply": f"未发送：{problem}。请先把候选人邮箱换成真实地址再发（当前：{draft['to']}）。"}

    # 每日发送限额：防止误操作或脚本刷量把邮箱额度打满（DAILY_SEND_LIMIT，默认 100）
    if DAILY_SEND_LIMIT > 0:
        sent_today = _sent_today()
        if sent_today >= DAILY_SEND_LIMIT:
            return {**state, "reply": f"未发送：今天已发出 {sent_today} 封，达到每日上限 {DAILY_SEND_LIMIT} 封。"
                                      f"如确需继续，请调高 .env 里的 DAILY_SEND_LIMIT 或明天再发。"}

    ok = send_with_retry(draft["to"], draft["subject"], draft["body"])
    status = "sent" if ok else "failed"
    pool.execute(
        "UPDATE contact_records SET send_status=%s, email_subject=%s, email_body=%s, "
        "sent_at=IF(%s='sent', NOW(), NULL) WHERE id=%s",
        (status, draft["subject"], draft["body"], status, draft["record_id"]),
    )
    if ok:
        pool.execute("UPDATE candidates SET status = '已联系' WHERE id = "
                     "(SELECT candidate_id FROM contact_records WHERE id = %s)", (draft["record_id"],))
        return {**state, "reply": f"邮件已发送至 {draft['to']}，候选人状态已更新为「已联系」。"}
    return {**state, "reply": "邮件发送失败（已重试3次），send_status 已标记为 failed，请检查 SMTP 配置。"}
