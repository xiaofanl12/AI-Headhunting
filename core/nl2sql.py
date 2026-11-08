"""NL2SQL：Prompt 生成 + 安全校验器（LLM 之外的第二道门）。

三层防护：本模块校验 -> 最小权限 DB 账号 -> query_logs 审计。
"""
import re

import sqlparse

from core.llm import llm

# 注入给 LLM 的表结构（只含业务必需信息，精简以提高生成准确率）
DDL_SCHEMA = """\
companies(id, name, industry, scale, introduction)
positions(id, company_id, title, skill_tags JSON, min_years, salary_range, status)  -- status: 1招聘中 0关闭
candidates(id, name, gender, phone, email, resume_text, skill_tags JSON, years_of_exp, education, status)
  -- gender: 男/女；status 枚举: 待筛选/待推荐/已推荐/已联系/面试中/已入职/已放弃
contact_records(id, candidate_id, position_id, match_score, skill_score, exp_score, reason,
                email_subject, email_body, send_status, sent_at)  -- send_status: pending/sent/failed
"""

FEW_SHOTS = """\
示例1: 查询有5年以上经验的Python候选人 -> SELECT * FROM candidates WHERE skill_tags LIKE '%Python%' AND years_of_exp >= 5
示例2: 新增一个候选人 -> INSERT INTO candidates (name, email, skill_tags, years_of_exp) VALUES ('张三', 'z@x.com', '["Python","SQL"]', 6)
示例3: 把候选人1状态改为已联系 -> UPDATE candidates SET status = '已联系' WHERE id = 1
"""

SYSTEM_PROMPT = (
    "你是MySQL专家。根据用户指令生成一条SQL，规则：\n"
    "1. 只输出SQL本身，不要任何解释或Markdown代码块\n"
    "2. 表结构如下:\n" + DDL_SCHEMA +
    "3. UPDATE/DELETE 必须带 WHERE 且尽量用 id\n"
    "4. 禁止 DDL/DCL 语句\n\n" + FEW_SHOTS
)

_FORBIDDEN = re.compile(r"\b(truncate|drop|alter|grant|revoke|create)\b", re.I)
_MULTI_STMT = re.compile(r";\s*\S")
_COMMENT = re.compile(r"--|#|/\*")

# 查询结果行数上限：超出截断，避免大结果集撑爆内存与连接池
MAX_ROWS = 200


def validate_sql(sql: str, allowed: tuple[str, ...] = ("select", "insert", "update", "delete")) -> tuple[bool, str]:
    """安全校验器。返回 (是否通过, 说明)。纯函数，可单测。"""
    sql = (sql or "").strip().rstrip(";")
    if not sql:
        return False, "空语句"
    if _MULTI_STMT.search(sql):
        return False, "禁止多语句"
    if _FORBIDDEN.search(sql) or _COMMENT.search(sql):
        return False, "包含禁止的关键字或注释"
    try:
        stmt_type = sqlparse.parse(sql)[0].get_type().lower()
    except Exception:
        return False, "SQL 解析失败"
    if stmt_type not in allowed:
        return False, f"语句类型 {stmt_type} 不在允许范围 {allowed}"
    if stmt_type in ("update", "delete") and not re.search(r"\bwhere\b", sql, re.I):
        return False, "UPDATE/DELETE 必须带 WHERE"
    return True, stmt_type


def _extract_sql(text: str) -> str:
    """从 LLM 回复中剥离 Markdown 代码块与多余说明。"""
    m = re.search(r"```(?:sql)?\s*(.+?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def ensure_limit(sql: str, limit: int = MAX_ROWS) -> str:
    """给 SELECT 补 LIMIT，防止全表扫描拖出百万行。

    已有 LIMIT 的语句保持原样（不覆盖用户/LLM 的意图），非 SELECT 不处理。
    纯函数，可单测。
    """
    stripped = (sql or "").strip().rstrip(";")
    if not stripped or not stripped.lower().startswith("select"):
        return stripped
    if re.search(r"\blimit\s+\d+", stripped, re.I):
        return stripped
    return f"{stripped} LIMIT {limit}"


def generate_sql(user_input: str, allowed: tuple[str, ...] = ("select", "insert", "update", "delete")) -> str | None:
    """生成并校验 SQL；不通过则把报错回喂重试，最多 2 次；仍失败返回 None。"""
    if not llm.available:
        return None
    prompt = user_input
    for _ in range(3):
        raw = llm.chat(prompt, system=SYSTEM_PROMPT)
        if raw is None:
            return None
        sql = _extract_sql(raw)
        ok, msg = validate_sql(sql, allowed)
        if ok:
            return sql.rstrip(";")
        prompt = f"{user_input}\n\n你上一次生成的SQL未通过校验（原因：{msg}），请重新生成，只输出SQL。"
    return None
