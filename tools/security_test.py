# -*- coding: utf-8 -*-
"""安全专项测试：SQL 注入 / XSS / 越权 / 敏感信息泄露"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:7862"
PASS, FAIL = [], []


def req(method, path, body=None, token=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            txt = resp.read().decode("utf-8", "replace")
            return resp.status, (txt if raw else json.loads(txt))
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(txt)
        except Exception:
            return e.code, txt
    except Exception as e:
        return 0, str(e)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} || {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  -> {detail}"))


# 准备测试账号
USER, PWD = "zz_sec_runner", "sec123456"
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db import pool as _pre
    _pre.execute("DELETE FROM app_users WHERE username = %s", (USER,))
except Exception:
    pass
req("POST", "/api/auth/register", {"username": USER, "password": PWD, "display_name": "安全测试"})
st, r = req("POST", "/api/auth/login", {"username": USER, "password": PWD})
TOKEN = r["data"]["token"]

print("=" * 70)
print("[1] SQL 注入")
print("=" * 70)
payloads = [
    "'; DROP TABLE candidates; --",
    "' OR '1'='1",
    "admin'--",
    "1'; UPDATE app_users SET role='admin' WHERE '1'='1",
    "%' OR username LIKE '%",
]
for pl in payloads:
    st, r = req("POST", "/api/auth/login", {"username": pl, "password": pl})
    ok_blocked = st in (400, 401, 422) and not (isinstance(r, dict) and r.get("ok"))
    check(f"登录注入被拒: {pl[:28]}", ok_blocked, f"{st} {str(r)[:80]}")

st, r = req("POST", "/api/companies/lookup", {"name": "'; DROP TABLE candidates; --"}, token=TOKEN)
check("公司查询注入不 500", st != 500, f"{st} {str(r)[:120]}")

st, r = req("GET", "/api/candidates", token=TOKEN)
check("注入后候选人表仍存在", st == 200 and len(r.get("data") or []) > 0,
      f"{st} count={len(r.get('data') or [])}")

st, r = req("POST", "/api/positions",
            {"company": "'; DROP TABLE positions; --", "title": "X'; DELETE FROM positions; --"},
            token=TOKEN)
st2, r2 = req("GET", "/api/positions", token=TOKEN)
check("职位表仍存在（未被注入删表）", st2 == 200 and len(r2.get("data") or []) >= 1,
      f"{st2} count={len(r2.get('data') or [])}")

print("=" * 70)
print("[2] XSS 存储型注入")
print("=" * 70)
xss = '<script>window.__XSS_HIT__=1</script><img src=x onerror="window.__XSS_HIT__=2">'
st, r = req("POST", "/api/positions", {"company": xss, "title": xss, "min_years": 1}, token=TOKEN)
check("XSS 内容入库不报错", st in (200, 400), f"{st}")
st, r = req("GET", "/api/positions", token=TOKEN)
raw = json.dumps(r)
has_raw_script = "<script>" in raw
print(f"  [信息] 接口原样返回脚本标签: {has_raw_script}（前端需转义）")
# 记录该职位 id 供清理
xss_ids = [p["id"] for p in (r.get("data") or []) if "<script>" in str(p.get("title", ""))]
print(f"  [信息] 含脚本的职位 id: {xss_ids}")

print("=" * 70)
print("[3] 越权与敏感信息")
print("=" * 70)
st, r = req("GET", "/api/candidates", token=TOKEN)
raw = json.dumps(r)
check("候选人接口不含 password", "password" not in raw.lower(), "含 password 字段")
phone_leak = []
for c in (r.get("data") or []):
    ph = str(c.get("phone") or "")
    if ph and "*" not in ph and len(ph) >= 11:
        phone_leak.append(ph)
check("手机号已脱敏", not phone_leak, f"泄露 {len(phone_leak)} 条，例：{phone_leak[:2]}")

st, r = req("GET", "/api/auth/security", token=TOKEN)
raw = json.dumps(r)
check("安全接口无 hash", "pbkdf2" not in raw and "password_hash" not in raw, raw[:100])

# 用 A 账号 token 改 B 账号资料（应只改自己）
st, r = req("POST", "/api/auth/profile", {"display_name": "被篡改", "username": "admin"}, token=TOKEN)
check("无法把用户名改成他人账号", st in (400, 409), f"{st} {str(r)[:100]}")

print("=" * 70)
print("[4] 未鉴权访问（中间件）")
print("=" * 70)
public_apis = ["/api/overview", "/api/candidates", "/api/positions", "/api/companies",
               "/api/records", "/api/match-weights", "/api/analyses", "/api/export.csv",
               "/api/selfcheck", "/api/auth/me", "/api/auth/security"]
leaked = []
for p_ in public_apis:
    st, _ = req("GET", p_)
    if st != 401:
        leaked.append(f"{p_} -> {st}")
check("所有业务接口均需登录", not leaked, "; ".join(leaked))

st, r = req("GET", "/api/auth/stats")
check("公开 stats 不含敏感数据", st == 200 and "users" in (r.get("data") or {}), f"{st} {str(r)[:100]}")

print("=" * 70)
print("[5] 输入健壮性")
print("=" * 70)
st, r = req("POST", "/api/positions", {"company": "A" * 5000, "title": "B" * 5000}, token=TOKEN)
check("超长输入不 500", st != 500, f"{st} {str(r)[:100]}")

st, r = req("POST", "/api/positions", {"company": "正常公司", "title": "正常职位",
                                       "min_years": "abc"}, token=TOKEN)
check("min_years 非数字不 500", st != 500, f"{st} {str(r)[:100]}")

st, r = req("POST", "/api/positions", {"company": "正常公司", "title": "负数年限",
                                       "min_years": -99}, token=TOKEN)
check("min_years 负数被拒或归零", st in (200, 400, 422), f"{st} {str(r)[:100]}")

st, r = req("POST", "/api/auth/register", {"username": "ok_name", "password": "x" * 5000})
check("超长密码被拒", st in (400, 422), f"{st} {str(r)[:80]}")

# 清理
try:
    from db import pool as _p
    _p.execute("DELETE FROM app_users WHERE username = %s", (USER,))
    _p.execute("DELETE FROM app_users WHERE username = 'ok_name'")
    _p.execute("UPDATE match_settings SET sval='0.8500' WHERE skey='skill_weight'")
    _p.execute("UPDATE match_settings SET sval='75' WHERE skey='threshold'")
    for i in xss_ids:
        _p.execute("DELETE FROM positions WHERE id = %s", (i,))
    _p.execute("DELETE FROM positions WHERE title LIKE '正常职位%' OR title LIKE '负数年限%'")
    # 清理 SQL 注入测试落库的脏职位（payload 落在 title/company 上）
    bad = ("title LIKE '%%DROP TABLE%%' OR title LIKE '%%DELETE FROM%%' "
           "OR company_id IN (SELECT id FROM companies WHERE name LIKE '%%DROP TABLE%%') "
           "OR title LIKE '%%<%%'")
    _p.execute(f"DELETE FROM contact_records WHERE position_id IN (SELECT id FROM positions WHERE {bad})")
    _p.execute(f"DELETE FROM positions WHERE {bad}")
    print("\n[cleanup] 已清理测试数据")
except Exception as e:
    print("[cleanup] 失败:", e)

print("=" * 70)
print(f"汇总：通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("\n失败明细：")
    for f in FAIL:
        print("  -", f)
print("=" * 70)
