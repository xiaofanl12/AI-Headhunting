"""合规风险核查：企查查三个核查接口 + 名称解析 + 结果缓存。

为什么单独一层
--------------
1. 核查接口按次计费，免费额度有限，同一家企业 30 天内只查一次；
2. 核查接口只认工商全名，而库里存的是「智谱」这类简称——先从企业简介里推断全名再查，
   推断不出来就如实标注「名称非工商全名」，不能把「查不到公司」说成「没有风险」；
3. 核查结果要能追溯（哪天查的、用的什么名字），所以落库而不是只放内存。

对外接口：get_risk(company_id, company_name, intro="", refresh=False) -> dict
"""
import json
import re

from crawler import qcc_client
from db import pool

RISK_TTL_DAYS = 30

_FULL_NAME_HINTS = ("公司", "厂", "中心", "研究院", "事务所", "合作社", "集团", "学校", "医院")

# 从简介里捞工商全名：北京智谱华章科技股份有限公司 / 上海某某有限公司
_FULL_NAME_RE = re.compile(r"[\u4e00-\u9fa5（）()]{2,30}?(?:股份有限公司|有限责任公司|有限公司)")


def looks_like_full_name(name: str) -> bool:
    return any(h in (name or "") for h in _FULL_NAME_HINTS)


def guess_full_name(name: str, intro: str = "") -> str:
    """优先用简介里的工商全名，其次用原名称。"""
    name = (name or "").strip()
    if looks_like_full_name(name):
        return name
    m = _FULL_NAME_RE.search(intro or "")
    return m.group(0) if m else name


def _latest(company_id: int | None, company_name: str):
    if company_id:
        rows = pool.query(
            "SELECT * FROM company_risk_checks WHERE company_id = %s "
            "ORDER BY id DESC LIMIT 1", (company_id,))
        if rows:
            return rows[0]
    rows = pool.query(
        "SELECT * FROM company_risk_checks WHERE company_name = %s "
        "ORDER BY id DESC LIMIT 1", (company_name,))
    return rows[0] if rows else None


def _payload(row) -> dict | None:
    try:
        payload = json.loads(row["detail"] or "{}")
    except Exception:
        return None
    if not payload.get("items"):
        return None
    at = row.get("checked_at")
    payload["checked_at"] = at.strftime("%Y-%m-%d %H:%M") if hasattr(at, "strftime") else str(at or "")
    payload["from_cache"] = True
    return payload


def _age_days(row) -> float:
    from datetime import datetime
    at = row.get("checked_at")
    if not hasattr(at, "strftime"):
        return 9999.0
    return (datetime.now() - at).total_seconds() / 86400


def _save(company_id: int | None, company_name: str, risk: dict):
    items = {i["key"]: i for i in risk.get("items", [])}
    try:
        pool.execute(
            "INSERT INTO company_risk_checks (company_id, company_name, shixin_count, "
            "exception_count, penalty_count, detail, error) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (company_id, company_name,
             (items.get("shixin") or {}).get("count", 0),
             (items.get("exception") or {}).get("count", 0),
             (items.get("penalty") or {}).get("count", 0),
             json.dumps(risk, ensure_ascii=False),
             (risk.get("error") or "")[:250]))
    except Exception:
        pass  # 缓存失败不影响本次结果


def get_risk(company_id: int | None, company_name: str, intro: str = "",
             refresh: bool = False, max_age_days: int = RISK_TTL_DAYS) -> dict:
    """取风险核查结果：优先用未过期缓存，否则按工商全名调接口并落库。"""
    company_name = (company_name or "").strip()
    if not company_name:
        return {"available": False, "company": "", "checked_at": None,
                "items": [], "error": "缺少企业名称"}

    if not refresh:
        row = _latest(company_id, company_name)
        if row and _age_days(row) <= max_age_days:
            payload = _payload(row)
            if payload:
                payload["name_hint"] = _name_hint(payload)
                return payload

    full_name = guess_full_name(company_name, intro)
    risk = qcc_client.risk_check(full_name)
    risk["queried_name"] = full_name
    risk["origin_name"] = company_name
    _save(company_id, full_name, risk)
    risk["from_cache"] = False
    risk["name_hint"] = _name_hint(risk)
    return risk


def _name_hint(risk: dict) -> str:
    """名称相关提示：非全名查不到时要说清，避免被当成「无风险」。"""
    queried = risk.get("queried_name") or risk.get("company") or ""
    origin = risk.get("origin_name") or queried
    if risk.get("error") == "未匹配到该企业，需使用工商全名":
        return f"「{queried}」未匹配到企业，风险核查未完成，需补充工商全名后重查"
    if not looks_like_full_name(queried):
        return f"「{queried}」不是工商全名，核查结果可能不完整"
    if queried != origin:
        return f"已按工商全名「{queried}」核查"
    return ""


def summarize_payload(risk: dict) -> dict:
    """给计分用的摘要：命中条数 + 是否真正核查过。"""
    s = qcc_client.summarize(risk or {})
    s["name_hint"] = (risk or {}).get("name_hint", "")
    return s
