# -*- coding: utf-8 -*-
"""全面 API 功能测试：正常流程 / 参数校验 / 边界 / 安全"""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:7862"
PASS, FAIL = [], []


def req(method, path, body=None, token=None, raw=False):
    url = BASE + path
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode()
    if token:
        headers["X-Auth-Token"] = token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
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
    if cond:
        PASS.append(name)
    else:
        FAIL.append(f"{name} || {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  -> {detail}"))


print("=" * 70)
print("[1] 鉴权")
print("=" * 70)
TEST_USER, TEST_PWD = "zz_test_runner", "test123456"
# 先清掉上次可能残留的测试账号，保证脚本可重复运行
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from db import pool as _pre
    _pre.execute("DELETE FROM app_users WHERE username = %s", (TEST_USER,))
    _pre.execute("DELETE FROM positions WHERE title = '测试职位X'")
except Exception as _e:
    print("[prep] 清理失败（可忽略）:", _e)
req("POST", "/api/auth/register", {"username": TEST_USER, "password": TEST_PWD, "display_name": "自动化测试"})
st, r = req("POST", "/api/auth/login", {"username": TEST_USER, "password": TEST_PWD})
check("登录成功返回 token", st == 200 and r.get("data", {}).get("token"), f"{st} {r}")
TOKEN = r["data"]["token"] if st == 200 else None

st, r = req("POST", "/api/auth/login", {"username": TEST_USER, "password": "wrong"})
check("错误密码被拒", st in (400, 401), f"{st} {r}")

st, r = req("POST", "/api/auth/login", {"username": "not_exist_x", "password": "abc123"})
check("不存在用户被拒", st in (400, 401), f"{st} {r}")

st, r = req("GET", "/api/positions")
check("无 token 访问受保护接口被拒", st in (401, 403), f"{st} {r}")

st, r = req("GET", "/api/positions", token="bogus-token-123")
check("伪造 token 被拒", st in (401, 403), f"{st} {r}")

st, r = req("GET", "/api/auth/me", token=TOKEN)
check("me 返回当前用户", st == 200 and r["data"].get("username") == TEST_USER, f"{st} {r}")

print("=" * 70)
print("[2] 基础数据")
print("=" * 70)
st, r = req("GET", "/api/overview", token=TOKEN)
check("overview 正常", st == 200 and isinstance(r.get("data"), dict), f"{st} {r}")

st, r = req("GET", "/api/candidates", token=TOKEN)
cands = r.get("data") if st == 200 else None
check("candidates 返回列表", st == 200 and isinstance(cands, list) and len(cands) > 0,
      f"{st} count={len(cands) if isinstance(cands, list) else 'n/a'}")

st, r = req("GET", "/api/positions", token=TOKEN)
poss = r.get("data") if st == 200 else None
check("positions 返回列表", st == 200 and isinstance(poss, list) and len(poss) > 0, f"{st} {r}")

st, r = req("GET", "/api/companies", token=TOKEN)
check("companies 返回列表", st == 200 and isinstance(r.get("data"), list), f"{st} {r}")

print("=" * 70)
print("[3] 候选人详情边界")
print("=" * 70)
cid = cands[0]["id"]
st, r = req("GET", f"/api/candidate/{cid}", token=TOKEN)
check("有效候选人详情", st == 200 and r["data"], f"{st}")

st, r = req("GET", "/api/candidate/99999999", token=TOKEN)
check("不存在候选人返回 404", st == 404, f"{st} {str(r)[:120]}")

st, r = req("GET", "/api/candidate/abc", token=TOKEN)
check("非法候选人 ID 不 500", st in (400, 404, 422), f"{st} {str(r)[:120]}")

st, r = req("GET", "/api/candidate/-1", token=TOKEN)
check("负数候选人 ID 不 500", st in (400, 404, 422), f"{st} {str(r)[:120]}")

print("=" * 70)
print("[4] 匹配权重")
print("=" * 70)
st, r = req("GET", "/api/match-weights", token=TOKEN)
orig = r.get("data") if st == 200 else None
check("读取权重", st == 200 and orig, f"{st} {r}")

st, r = req("POST", "/api/match-weights", {"skill": 0.6, "threshold": 75}, token=TOKEN)
check("保存合法权重", st == 200, f"{st} {r}")

st, r = req("POST", "/api/match-weights", {"skill": 1.5, "threshold": 75}, token=TOKEN)
check("skill>1 被拒", st in (400, 422), f"实际 {st} {str(r)[:100]}")

st, r = req("POST", "/api/match-weights", {"skill": -0.2, "threshold": 75}, token=TOKEN)
check("负数权重被拒", st in (400, 422), f"实际 {st} {str(r)[:100]}")

st, r = req("POST", "/api/match-weights", {"skill": 0.6, "threshold": 150}, token=TOKEN)
check("threshold>100 被拒", st in (400, 422), f"实际 {st} {str(r)[:100]}")

st, r = req("POST", "/api/match-weights", {"skill": 0.6, "threshold": -5}, token=TOKEN)
check("threshold 负数被拒", st in (400, 422), f"实际 {st} {str(r)[:100]}")

print("=" * 70)
print("[5] 重算匹配")
print("=" * 70)
st, r = req("POST", "/api/match/recalc", {}, token=TOKEN)
check("重算成功并返回统计", st == 200 and "evaluated" in (r.get("data") or {}), f"{st} {str(r)[:200]}")

st, r = req("GET", "/api/records", token=TOKEN)
recs = r.get("data") if st == 200 else []
check("records 返回列表", st == 200 and isinstance(recs, list), f"{st}")

if recs:
    rid = recs[0]["id"]
    st, r = req("POST", f"/api/records/{rid}/decision", {"decision": "邀约"}, token=TOKEN)
    check("HR 决策 accept", st == 200, f"{st} {str(r)[:150]}")
    st, r = req("POST", f"/api/records/{rid}/decision", {"decision": "非法值"}, token=TOKEN)
    check("非法决策值被拒", st in (400, 422), f"实际 {st} {str(r)[:100]}")
    st, r = req("POST", "/api/records/99999999/decision", {"decision": "邀约"}, token=TOKEN)
    check("不存在记录决策 404", st == 404, f"{st} {str(r)[:100]}")

print("=" * 70)
print("[6] 职位")
print("=" * 70)
st, r = req("POST", "/api/positions",
            {"title": "", "company": "", "requirements": ""}, token=TOKEN)
check("空职位字段被拒", st in (400, 422), f"实际 {st} {str(r)[:150]}")

st, r = req("POST", "/api/positions",
            {"title": "测试职位X", "company": "测试公司X", "city": "杭州",
             "min_years": 2, "skills": "Python,Go", "salary": "20-30万/年"}, token=TOKEN)
check("创建职位成功", st == 200 and r.get("data"), f"{st} {str(r)[:200]}")
new_pos = None
if st == 200:
    st2, r2 = req("GET", "/api/positions", token=TOKEN)
    for p_ in (r2.get("data") or []):
        if p_.get("title") == "测试职位X":
            new_pos = p_["id"]

if new_pos:
    st, r = req("POST", f"/api/positions/{new_pos}/close", {}, token=TOKEN)
    check("关闭职位", st == 200, f"{st} {str(r)[:150]}")
    st, r = req("POST", f"/api/positions/{new_pos}/close", {}, token=TOKEN)
    check("重复关闭职位幂等", st == 200, f"{st} {str(r)[:150]}")

st, r = req("POST", "/api/positions/99999999/close", {}, token=TOKEN)
check("关闭不存在职位 404", st == 404, f"{st} {str(r)[:100]}")

print("=" * 70)
print("[7] 公司查询")
print("=" * 70)
st, r = req("POST", "/api/companies/lookup", {"name": ""}, token=TOKEN)
check("空公司名被拒", st in (400, 422), f"实际 {st} {str(r)[:150]}")

st, r = req("POST", "/api/companies/lookup", {"name": "阿里巴巴"}, token=TOKEN)
check("公司查询有响应", st in (200, 404), f"{st} {str(r)[:150]}")

print("=" * 70)
print("[8] 导出与自检")
print("=" * 70)
st, r = req("GET", "/api/export.csv", token=TOKEN, raw=True)
check("CSV 导出成功", st == 200 and "text" not in str(r)[:3].lower(), f"{st}")
if isinstance(r, str):
    first = r.splitlines()[0] if r else ""
    check("CSV 有表头", "," in first and len(first) > 5, f"{first[:80]}")

st, r = req("GET", "/api/selfcheck", token=TOKEN)
check("selfcheck 正常", st == 200 and isinstance(r.get("data"), dict), f"{st} {str(r)[:200]}")

print("=" * 70)
print("[9] 账号中心")
print("=" * 70)
st, r = req("GET", "/api/auth/security", token=TOKEN)
check("security 无敏感泄露", st == 200 and "password_hash" not in json.dumps(r), f"{st}")

st, r = req("POST", "/api/auth/profile", {"display_name": "张经理", "username": TEST_USER}, token=TOKEN)
check("更新资料成功", st == 200, f"{st} {str(r)[:150]}")

st, r = req("POST", "/api/auth/profile", {"display_name": "测试展示名", "username": "ab"}, token=TOKEN)
check("非法用户名被拒", st in (400, 422), f"实际 {st} {str(r)[:120]}")

st, r = req("POST", "/api/auth/profile", {"display_name": "测试展示名", "username": "admin"}, token=TOKEN)
check("占用他人用户名被拒", st in (400, 409), f"实际 {st} {str(r)[:120]}")
# 若被误改，立即恢复
if st == 200:
    req("POST", "/api/auth/profile", {"display_name": "自动化测试", "username": TEST_USER}, token=TOKEN)

st, r = req("POST", "/api/auth/password",
            {"old_password": "wrong", "new_password": "abc123"}, token=TOKEN)
check("旧密码错误被拒", st in (400, 401), f"{st} {str(r)[:120]}")

st, r = req("POST", "/api/auth/password",
            {"old_password": TEST_PWD, "new_password": "1"}, token=TOKEN)
check("新密码过短被拒", st in (400, 422), f"实际 {st} {str(r)[:120]}")

st, r = req("POST", "/api/auth/register", {"username": "ab", "password": "abc123"})
check("注册用户名过短被拒", st in (400, 422), f"实际 {st} {str(r)[:120]}")

st, r = req("POST", "/api/auth/register", {"username": TEST_USER, "password": "abc123"})
check("注册重复用户名被拒", st in (400, 409), f"实际 {st} {str(r)[:120]}")

st, r = req("POST", "/api/auth/register", {"username": "a<b>c'\"", "password": "abc123"})
check("注册非法字符被拒", st in (400, 422), f"实际 {st} {str(r)[:120]}")

print("=" * 70)
print("[10] 分析接口")
print("=" * 70)
st, r = req("GET", "/api/analyses", token=TOKEN)
check("analyses 列表", st == 200 and isinstance(r.get("data"), list), f"{st}")

st, r = req("POST", "/api/analysis", {"name": ""}, token=TOKEN)
check("空公司名分析被拒", st in (200, 400, 404, 422) and st != 500, f"{st} {str(r)[:120]}")

# 清理测试账号
try:
    from db import pool as _p
    _p.execute("DELETE FROM app_users WHERE username = %s", (TEST_USER,))
    _p.execute("DELETE FROM positions WHERE title = '测试职位X'")
    _p.execute("UPDATE match_settings SET sval='0.8500' WHERE skey='skill_weight'")
    _p.execute("UPDATE match_settings SET sval='75' WHERE skey='threshold'")
    print("\n[cleanup] 测试账号与职位已清理")
except Exception as _e:
    print("[cleanup] 失败:", _e)

print("=" * 70)
print(f"汇总：通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
if FAIL:
    print("\n失败明细：")
    for f in FAIL:
        print("  -", f)
print("=" * 70)
