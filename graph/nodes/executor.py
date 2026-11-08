"""NL2SQL 节点 + SQL 执行节点（含 query_logs 审计）。"""
import json
import re

from core import nl2sql
from db import pool
from graph.nodes.intent_router import classify  # noqa: F401
from graph.state import GraphState

# 批量删除/清表类危险请求（不依赖 LLM 判断，防止提示注入绕过）
_DANGEROUS = re.compile(
    r"(删除|清空|移除|drop|truncate)[^，。；]{0,8}(所有|全部|整个|全表|库|表)"
    r"|truncate\s+table|drop\s+table|delete\s+from\s+\w+\s*;?\s*$",
    re.I,
)

# DDL 请求（建表/删表/改表结构）：业务上完全用不到，直接拒绝比让模型"自己想办法"更清楚
_DDL_REQUEST = re.compile(r"\b(drop|truncate|alter|create)\b|删表|清表|建表|改表结构|清库", re.I)

# 单条查询的执行超时（毫秒）
QUERY_TIMEOUT_MS = 5000


def nl2sql_node(state: GraphState) -> GraphState:
    intent = state["intent"]
    # 批量删除/清表类请求直接拒答（不依赖 LLM 判断，避免提示注入绕过）
    if _DANGEROUS.search(state["user_input"]):
        return {**state, "reply": "出于数据安全考虑，我不执行批量删除或清表操作。"
                                  "如需删除，请指定具体记录，例如「删除候选人 12」。"}
    # DDL 请求（drop/truncate/alter/create）一律拒绝：系统只做业务数据的增删改查
    if _DDL_REQUEST.search(state["user_input"]):
        return {**state, "reply": "系统不支持建表、删表或修改表结构这类操作（DDL 已全部禁用），"
                                  "只处理候选人、职位、公司等业务数据的查询与增删改。"
                                  "如需清理数据，请指定具体记录，例如「删除候选人 12」。"}
    # 只读意图仅允许 SELECT；写意图放开增删改，但校验器仍会拦截无 WHERE 的 UPDATE/DELETE
    allowed = ("select",) if intent == "query" else ("select", "insert", "update", "delete")
    sql = nl2sql.generate_sql(state["user_input"], allowed=allowed)
    if sql is None:
        return {**state, "reply": "抱歉，这条指令我暂时无法转换成安全的数据库操作，请换个说法或拆得更具体一些。"}
    return {**state, "sql": sql}


def executor_node(state: GraphState) -> GraphState:
    sql = state.get("sql")
    if not sql:
        return state
    is_select = sql.lstrip().lower().startswith("select")
    # SELECT 强制补 LIMIT（已有则保留），避免全表扫描返回海量数据
    sql_exec = nl2sql.ensure_limit(sql) if is_select else sql
    try:
        if is_select:
            rows = pool.query(sql_exec, timeout_ms=QUERY_TIMEOUT_MS)
            result = {"rows": rows, "limited": sql_exec != sql}
        else:
            affected = pool.execute(sql_exec)
            result = {"affected": affected}
        pool.execute(
            "INSERT INTO query_logs (session_id, user_input, generated_sql, executed, affected_rows) "
            "VALUES (%s,%s,%s,1,%s)",
            (state.get("session_id", ""), state["user_input"], sql_exec,
             result.get("affected", len(result.get("rows", [])))),
        )
        return {**state, "sql": sql_exec, "query_result": [result]}
    except Exception as e:
        msg = str(e)
        if "3024" in msg or "max_execution_time" in msg.lower():
            return {**state, "reply": f"查询超时（超过 {QUERY_TIMEOUT_MS / 1000:.0f} 秒），请缩小查询范围或加上更具体的筛选条件。"}
        return {**state, "reply": f"SQL 执行出错：{msg[:200]}"}


# 结果字段的中文名（覆盖 candidates/positions/companies/contact_records 常用列）
_LABELS = {
    "id": "编号", "name": "姓名", "gender": "性别", "phone": "电话", "email": "邮箱",
    "years_of_exp": "工作年限", "education": "学历", "status": "状态",
    "skill_tags": "技能标签", "resume_text": "简历摘要", "data_source": "数据来源",
    "title": "职位名称", "company_id": "所属公司", "min_years": "经验要求",
    "salary_range": "薪资范围", "industry": "所属行业", "scale": "公司规模",
    "introduction": "公司简介", "match_score": "匹配总分", "skill_score": "技能得分",
    "exp_score": "经验得分", "reason": "推荐理由", "send_status": "邮件状态",
    "sent_at": "发送时间", "created_at": "记录时间",
    # 聚合查询常见别名
    "cnt": "数量", "count": "数量", "n": "数量", "total": "合计",
    "avg": "均值", "avg_score": "平均分", "max": "最大", "min": "最小",
    "company": "公司", "company_name": "公司", "pos_n": "职位数", "num": "数量",
}
# 枚举值翻译
_VALUE_MAPS = {
    "send_status": {"pending": "待发送", "sent": "已发送", "failed": "发送失败"},
    "data_source": {"resume_ner": "公开简历语料", "manual": "人工录入"},
    "status": {1: "招聘中", 0: "已关闭", "1": "招聘中", "0": "已关闭"},
}
# 每行展示的字段数（超过则换行）
_FIELDS_PER_LINE = 2
# 字段展示顺序（未列出的字段按原顺序追加在后面）
_FIELD_ORDER = [
    "gender", "education", "years_of_exp", "skill_tags", "phone", "email",
    "title", "salary_range", "min_years", "industry", "scale",
    "match_score", "reason", "introduction", "data_source", "created_at", "sent_at",
]


def _mask_phone(v: str) -> str:
    p = str(v)
    return p[:3] + "****" + p[-4:] if len(p) >= 7 else "****"


def _fmt_skills(v) -> str:
    """技能标签统一转成「Python、MySQL」形式（兼容 JSON 字符串 / 列表 / 逗号分隔）。"""
    if isinstance(v, (list, tuple)):
        vals = list(v)
    else:
        s = str(v).strip()
        try:
            loaded = json.loads(s)
            vals = loaded if isinstance(loaded, list) else [s]
        except Exception:
            vals = [x for x in re.split(r"[,，、;；\s]+", s) if x]
    vals = [str(x).strip().strip('"') for x in vals if str(x).strip().strip('"')]
    return "、".join(vals) if vals else "未填写"


def _fmt_years(v) -> str:
    try:
        f = float(v)
        return f"{int(f)} 年" if f == int(f) else f"{f:.1f} 年"
    except (TypeError, ValueError):
        return str(v)


def _fmt_value(key: str, value) -> str:
    """把数据库字段值转成用户可读的中文片段。"""
    if value is None or value == "":
        return "未填写"
    key = key.lower()
    if key == "phone":
        return _mask_phone(value)
    if key == "skill_tags":
        return _fmt_skills(value)
    if key in ("years_of_exp", "min_years"):
        return _fmt_years(value)
    mapping = _VALUE_MAPS.get(key)
    if mapping and value in mapping:
        return mapping[value]
    if key == "resume_text":
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text[:60] + "…" if len(text) > 60 else (text or "未填写")
    if key == "introduction":
        text = re.sub(r"\s+", " ", str(value)).strip()
        return text[:80] + "…" if len(text) > 80 else text
    return str(value)


def _fmt_row(row: dict, idx: int) -> str:
    """单行结果 -> Markdown 候选人卡片：姓名标题 + 状态徽标 + 字段列表 + 简历链接。"""
    item = dict(row)
    name = item.pop("name", None)
    cid = item.pop("id", None) if name else None
    resume = item.pop("resume_text", None)
    status = item.pop("status", None) if name else None

    if name:
        head = f"**{idx}. {name}**"
        badges = []
        if cid is not None:
            badges.append(f"编号 {cid}")
        if status not in (None, ""):
            badges.append(str(status))
        if badges:
            head += "　" + "　".join(f"`{b}`" for b in badges)
    else:
        head = f"**{idx}.**"
        status = None

    # 按预设顺序排列字段，未列出的按原顺序追加
    keys = [k for k in _FIELD_ORDER if k in item] + \
           [k for k in item if k not in _FIELD_ORDER]
    fields = [f"**{_LABELS.get(k.lower(), k)}**：{_fmt_value(k, item[k])}" for k in keys
              if not (k == "resume_text" and (item[k] in (None, "")))]

    lines = [head]
    for i in range(0, len(fields), _FIELDS_PER_LINE):
        lines.append("- " + "　|　".join(fields[i:i + _FIELDS_PER_LINE]))
    if resume not in (None, "") and cid is not None:
        lines.append(f"- [查看完整简历](/resume/{cid})")
    return "\n".join(lines) if len(lines) > 1 else head


def _fmt_table(rows: list[dict]) -> str:
    """非候选人结果（统计、职位、公司等）用 Markdown 表格，比逐条卡片紧凑好读。"""
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys and k != "resume_text":
                keys.append(k)
    if not keys:
        return "查询结果为空。"
    head = "| # | " + " | ".join(_LABELS.get(k.lower(), k) for k in keys) + " |"
    sep = "|---|" + "|".join("---" for _ in keys) + "|"
    lines = [head, sep]
    for i, r in enumerate(rows, 1):
        cells = []
        for k in keys:
            text = _fmt_value(k, r.get(k))
            cells.append(text.replace("|", "／").replace("\n", " "))
        lines.append(f"| {i} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def format_rows(rows: list[dict]) -> str:
    """查询结果 -> 中文自然语言描述（含全部字段，手机号脱敏，候选人带简历链接）。"""
    if not rows:
        return "查询结果为空。"
    # 聚合查询（单行单列）：直接给一句结论
    if len(rows) == 1:
        only = dict(rows[0])
        if len(only) == 1:
            v = next(iter(only.values()))
            k = next(iter(only.keys())).lower()
            if isinstance(v, int) and k in ("cnt", "count", "n", "total", "num"):
                return f"共查询到 {v} 条符合条件的记录。"
            return f"查询结果：{v}"
    # 非候选人结果（没有 name 列）走表格，避免出现「**1.** 状态：已推荐 | cnt：1」这种空标题卡片
    if all("name" not in r for r in rows):
        return f"共 {len(rows)} 条结果：\n\n" + _fmt_table(rows)
    cards = "\n\n".join(_fmt_row(r if isinstance(r, dict) else dict(r), i)
                        for i, r in enumerate(rows, 1))
    prefix = f"共找到 {len(rows)} 位候选人：\n\n" if all("name" in r for r in rows) else ""
    return prefix + cards
