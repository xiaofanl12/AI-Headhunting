"""企查查开放平台客户端：公司背调 / 合规风险核查的权威数据源（可选，需配置 Key）。

鉴权要点（踩过的坑，别改回去）
------------------------------
1. Token 与 Timespan 必须放在 **HTTP Header** 里，放 query 会返回 115「身份验证错误或者已过期」；
2. key（AppKey）才放 query；
3. Token = MD5(key + Timespan + SecretKey) 的 32 位大写，Timespan 为秒级时间戳，有效期 5 分钟；
4. 接口路径按官方 dataApi 页给的来，例如工商详情是 /ECIInfoVerify/GetInfo，
   不是 /CreditBasic/GetBasicDetailsByName（后者 404）。

核查接口返回的三态（必须区分，否则会把「查不到公司」误报成「没有风险」）：
- ok            ：Status=200 且 VerifyResult=0 → 确实未命中；
- not_found     ：Status 非 200 且 Message 含「无结果」→ 名称不是工商全名，没匹配到企业；
- not_purchased ：Status=214 → 该接口未开通；
- error         ：网络异常 / 其他状态码。
"""
import hashlib
import json
import time

import requests

import config

_BASE = "https://api.qichacha.com"
_TIMEOUT = 10

_OK = "200"
_NOT_PURCHASED = "214"

# 三个风险核查接口：key -> (展示名, 路径)
RISK_APIS = (
    ("shixin", "失信核查", "/ShixinCheck/GetList"),
    ("exception", "经营异常核查", "/ExceptionCheck/GetList"),
    ("penalty", "行政处罚核查", "/AdminPenaltyCheck/GetList"),
)


def available() -> bool:
    return bool(config.QCC_API_KEY and config.QCC_API_SECRET)


def _headers() -> dict:
    ts = str(int(time.time()))
    raw = f"{config.QCC_API_KEY}{ts}{config.QCC_API_SECRET}"
    return {"Token": hashlib.md5(raw.encode()).hexdigest().upper(), "Timespan": ts}


def _call(path: str, params: dict) -> dict:
    """统一请求：{ok, status, message, result, paging}。"""
    if not available():
        return {"ok": False, "status": "no_key", "message": "未配置企查查 Key", "result": None}
    try:
        resp = requests.get(f"{_BASE}{path}",
                            params={"key": config.QCC_API_KEY, **params},
                            headers=_headers(), timeout=_TIMEOUT)
        data = resp.json()
    except Exception as exc:
        return {"ok": False, "status": "http_error",
                "message": f"{type(exc).__name__}", "result": None}

    status = str(data.get("Status") or "")
    if status != _OK:
        return {"ok": False,
                "status": "not_purchased" if status == _NOT_PURCHASED else status,
                "message": str(data.get("Message") or "")[:120], "result": None}
    return {"ok": True, "status": status, "message": str(data.get("Message") or ""),
            "result": data.get("Result"), "paging": data.get("Paging")}


# --------------------------------------------------------------------------- #
# 基础查询
# --------------------------------------------------------------------------- #
def search_company(keyword: str) -> dict | None:
    """模糊搜索公司，返回最匹配的一条（含 Name / CreditCode）。"""
    resp = _call("/FuzzySearch/GetList", {"searchKey": keyword})
    if not resp["ok"] or not resp["result"]:
        return None
    items = resp["result"] or []
    return items[0] if items else None


def basic_details(company_name: str) -> dict | None:
    """按企业全名查工商详情（股东、主要人员、变更记录等）。"""
    resp = _call("/ECIInfoVerify/GetInfo", {"searchKey": company_name})
    return resp["result"] if resp["ok"] else None


def compose_introduction(details: dict) -> str:
    """把工商登记字段组装成一段简介文本。"""
    fields = [
        ("法定代表人", "OperName"),
        ("注册资本", "RegistCapi"),
        ("成立日期", "StartDate"),
        ("经营状态", "Status"),
        ("统一社会信用代码", "CreditCode"),
        ("企业类型", "EconKind"),
        ("注册地址", "Address"),
        ("经营范围", "Scope"),
    ]
    parts = [f"{label}：{details.get(key)}" for label, key in fields if details.get(key)]
    if not parts:
        return ""
    return "；".join(parts) + "。（以上为工商登记信息，来自企查查开放平台）"


# --------------------------------------------------------------------------- #
# 风险核查
# --------------------------------------------------------------------------- #
def _fmt_shixin(item: dict) -> str:
    bits = [item.get("Liandate") or "", item.get("Anno") or "",
            item.get("Executegov") or "", item.get("Executestatus") or ""]
    amt = item.get("Amount")
    if amt:
        bits.append(f"标的 {amt} 元")
    return " · ".join(b for b in bits if b)


def _fmt_exception(item: dict) -> str:
    listed = " ".join(x for x in (item.get("AddDate") or "", item.get("AddReason") or "") if x)
    office = item.get("DecisionOffice") or ""
    removed = f"（已于 {item['RemoveDate']} 移出）" if item.get("RemoveDate") else "（仍在异常名录）"
    return " · ".join(x for x in (listed, office) if x) + removed


def _fmt_penalty(item: dict) -> str:
    amt = item.get("PunishAmt")
    money = f"罚款 {float(amt) / 10000:.1f} 万元" if amt not in (None, "", 0) else ""
    bits = [item.get("PunishDate") or "", item.get("PunishOffice") or "",
            item.get("DocNo") or "", (item.get("PunishResult") or money or "")[:60],
            (item.get("PunishReason") or "")[:50]]
    return " · ".join(b for b in bits if b)


_FORMATTERS = {"shixin": _fmt_shixin, "exception": _fmt_exception, "penalty": _fmt_penalty}


def _risk_one(key: str, label: str, path: str, company_name: str) -> dict:
    item = {"key": key, "label": label, "hit": False, "count": 0,
            "samples": [], "state": "error", "note": ""}
    resp = _call(path, {"searchKey": company_name})

    if not resp["ok"]:
        status, msg = resp["status"], resp["message"] or ""
        if status == "not_purchased":
            item.update(state="not_purchased", note="接口未开通")
        elif "无结果" in msg or "未查询到" in msg or "无数据" in msg:
            item.update(state="not_found", note="未匹配到该企业（需工商全名）")
        elif status == "no_key":
            item.update(state="not_purchased", note="未配置企查查 Key")
        else:
            item.update(state="error", note=msg or str(status))
        return item

    result = resp["result"] or {}
    data = result.get("Data") or []
    total = (resp.get("paging") or {}).get("TotalRecords") or len(data)
    item["state"] = "ok"
    item["hit"] = bool(result.get("VerifyResult")) or bool(data)
    item["count"] = int(total or 0)

    fmt = _FORMATTERS.get(key)
    seen = []
    for row in data[:2]:
        text = fmt(row) if fmt else json.dumps(row, ensure_ascii=False)[:80]
        text = text if len(text) <= 90 else text[:90] + "…"
        if text and text not in seen:
            seen.append(text)
    item["samples"] = seen
    return item


def risk_check(company_name: str) -> dict:
    """一次跑完三个风险核查，任一接口失败不影响其他。"""
    out = {"available": available(), "company": company_name,
           "checked_at": time.strftime("%Y-%m-%d %H:%M"), "items": [], "error": ""}
    if not available():
        out["error"] = "未配置企查查 Key，跳过风险核查"
        return out
    if not company_name:
        out["error"] = "缺少企业名称"
        return out

    for key, label, path in RISK_APIS:
        out["items"].append(_risk_one(key, label, path, company_name))

    states = {i["state"] for i in out["items"]}
    if states == {"not_found"}:
        out["error"] = "未匹配到该企业，需使用工商全名"
    elif states <= {"not_purchased", "error"}:
        out["error"] = "；".join(dict.fromkeys(i["note"] for i in out["items"]))
    return out


def summarize(risk: dict) -> dict:
    """压成计分用的摘要（纯函数，可单测）。"""
    items = risk.get("items") or []
    ok_items = [i for i in items if i.get("state") == "ok"]
    counts = {i["key"]: (i["count"] if i.get("hit") else 0) for i in ok_items}
    return {
        "shixin": counts.get("shixin", 0),
        "exception": counts.get("exception", 0),
        "penalty": counts.get("penalty", 0),
        "checked": bool(ok_items),
        "not_found": bool(items) and all(i.get("state") == "not_found" for i in items),
        "checked_at": risk.get("checked_at"),
        "error": risk.get("error") or "",
    }
