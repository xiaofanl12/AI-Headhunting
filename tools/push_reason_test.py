"""推送候选人 + 推选理由 端到端测试（自带服务进程，跑完自动关）。

只读为主：除了自己创建的测试记录，不改动任何业务数据；
SMTP 发送只验证拦截路径（未邀约 / 坏收件人 / 重复推送），不真的发信。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 7873
BASE = f"http://127.0.0.1:{PORT}"
TAG = "zz_push_test"
results = []

# 本机回环不走系统代理（开发机的 HTTP_PROXY 会拦住 127.0.0.1 请求）
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(path, method="GET", body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-Auth-Token", token)
    try:
        with _OPENER.open(req, timeout=90) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"ok": False, "error": raw[:200]}


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -> {detail}" if detail and not cond else ""))


def start_server():
    code = (f"import sys; sys.path.insert(0, {ROOT!r}); sys.path.insert(0, {os.path.join(ROOT, 'web')!r});"
            f"import uvicorn; from web.server import api;"
            f"uvicorn.run(api, host='127.0.0.1', port={PORT}, log_level='warning')")
    env = dict(os.environ)
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    for _ in range(50):
        time.sleep(0.6)
        try:
            with _OPENER.open(BASE + "/login", timeout=3) as r:
                if r.status == 200:
                    return p
        except Exception:
            if p.poll() is not None:
                print("服务启动失败：", p.stdout.read().decode()[:800])
                return None
    return p


def main():
    print("启动测试服务…")
    srv = start_server()
    if not srv:
        return 1
    try:
        return run()
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=8)
        except Exception:
            srv.kill()


def run():
    print("\n=== 1. 登录 ===")
    pw = os.getenv("TEST_PWD", "demo123")
    st, r = call("/api/auth/login", "POST", {"username": "demo", "password": pw})
    if not r.get("ok"):
        print("登录失败（可设 TEST_PWD 环境变量）：", r)
        return 1
    tk = r["data"]["token"]
    check("demo 账号登录", True)

    print("\n=== 2. 重算生成推荐记录（含理由）===")
    st, r = call("/api/match/recalc", "POST", token=tk)
    check("重算接口成功", r.get("ok"), str(r)[:200])
    if r.get("ok"):
        d = r["data"]
        print(f"     评估 {d['evaluated']} 人次 · 强烈推荐 {d['strong']} · 推荐 {d['recommended']}"
              f" · 硬性拦截 {d['hard_blocked']} · 保留人工理由 {d.get('reason_kept', 0)}")
        check("重算返回 reason_kept 字段", "reason_kept" in d)

    print("\n=== 3. /api/records 是否返回 reason ===")
    st, r = call("/api/records", token=tk)
    check("记录接口成功", r.get("ok"))
    recs = r.get("data") or []
    check("有推荐记录", len(recs) > 0, f"{len(recs)} 条")
    rec_id = None
    if recs:
        first = recs[0]
        need = {"reason", "reason_src", "email", "can_push", "skill_score", "exp_score"}
        check("记录含推选理由等新字段", need <= set(first.keys()),
              f"缺 {need - set(first.keys())}")
        with_reason = [x for x in recs if (x.get("reason") or "").strip()]
        check("重算后每条都有理由", len(with_reason) == len(recs), f"{len(with_reason)}/{len(recs)}")
        if with_reason:
            print(f"     样例理由：{with_reason[0]['reason'][:100]}…")
        rec_id = (with_reason or recs)[0]["id"]

    print("\n=== 4. 理由生成接口（规则模板，不耗 token）===")
    if rec_id:
        st, r = call(f"/api/records/{rec_id}/reason", "POST", {"use_llm": False, "force": True}, token=tk)
        check("规则模板生成成功", r.get("ok"), str(r)[:200])
        if r.get("ok"):
            d = r["data"]
            check("来源标记为 rule", d["source"] == "rule", d["source"])
            txt = d["reason"]
            check("理由含技能命中信息", "技能命中" in txt)
            check("理由含档位", "档位" in txt)
            check("理由含人工确认提示", ("人工确认" in txt or "自行判断" in txt))
            print(f"     {txt[:130]}…")

    print("\n=== 5. 缺记录 / 非法参数 ===")
    st, r = call("/api/records/99999999/reason", "POST", {"use_llm": False}, token=tk)
    check("不存在的记录返回 404", st == 404, str(st))
    if rec_id:
        st, r = call(f"/api/records/{rec_id}/reason", "PUT", {"reason": "  "}, token=tk)
        check("空理由被拒绝", not r.get("ok"), str(r)[:120])
        st, r = call(f"/api/records/{rec_id}/reason", "PUT", {"reason": "x" * 700}, token=tk)
        check("超长理由被拒绝", not r.get("ok"), str(r)[:120])

    print("\n=== 6. HR 手改理由，重算不覆盖 ===")
    manual = f"{TAG} 手写理由：候选人的 RAG 落地经验与岗位强相关，建议优先面谈。"
    # 挑一条「重算后仍会进入推荐」的记录：/api/records 按 id 倒序，
    # 末位那条分数最高，最不容易在下一次重算中被硬性门槛或阈值筛掉
    keep_id = (recs[-1]["id"] if recs else None)
    if keep_id:
        st, r = call(f"/api/records/{keep_id}/reason", "PUT", {"reason": manual}, token=tk)
        check("HR 手改成功", r.get("ok") and r["data"]["source"] == "hr", str(r)[:160])
        st, r = call("/api/match/recalc", "POST", token=tk)
        kept = (r.get("data") or {}).get("reason_kept", 0) if r.get("ok") else -1
        st, r2 = call("/api/records", token=tk)
        row = next((x for x in (r2.get("data") or []) if x["id"] == keep_id), None)
        check("重算后手写理由仍在", bool(row) and row["reason"] == manual,
              ((row or {}).get("reason") or "")[:60])
        check("来源仍是 hr", bool(row) and row["reason_src"] == "hr", (row or {}).get("reason_src"))
        check("重算统计 reason_kept > 0", kept > 0, str(kept))

    print("\n=== 7. 邮件预览接口 ===")
    if rec_id:
        st, r = call(f"/api/records/{rec_id}/email?use_llm=false", token=tk)
        check("邮件预览成功", r.get("ok"), str(r)[:200])
        if r.get("ok"):
            d = r["data"]
            check("含主题", bool(d.get("subject")))
            check("含正文", bool(d.get("body")))
            check("含推选理由", bool(d.get("reason")))
            check("规则兜底时正文附推选依据", ("推选依据" in d["body"]) or d["reason_source"] == "llm")
            check("返回收件人问题字段", "recipient_problem" in d)
            print(f"     主题：{d['subject']}  ->  {d['to'] or '（无邮箱）'}")
            if d.get("recipient_problem"):
                print(f"     收件人问题：{d['recipient_problem']}")

    print("\n=== 8. 推送拦截（不真发信）===")
    sent = [x for x in recs if x.get("send_status") == "sent"]
    if sent:
        st, r = call(f"/api/records/{sent[0]['id']}/push", "POST", {}, token=tk)
        check("已推送记录拒绝重复推送", not r.get("ok"), str(r)[:120])
    else:
        print("     （跳过：当前没有已发送记录）")

    if rec_id:
        st, r = call("/api/records", token=tk)
        row = next((x for x in (r.get("data") or []) if x["id"] == rec_id), None)
        if row and row.get("decision") != "邀约" and row.get("send_status") != "sent":
            st, r = call(f"/api/records/{rec_id}/push", "POST", {"confirm_decision": False}, token=tk)
            err = str(r.get("error") or "")
            check("未标邀约时拒绝推送", not r.get("ok"), err[:160])
            check("拦截原因是决策或邮箱", ("邀约" in err) or ("邮箱" in err), err[:160])
            print(f"     拦截提示：{err[:100]}")

    print("\n=== 9. 权限校验 ===")
    st, r = call("/api/records/1/reason", "POST", {"use_llm": False})
    check("未登录访问理由接口返回 401", st == 401, str(st))
    st, r = call("/api/records/1/push", "POST", {})
    check("未登录访问推送接口返回 401", st == 401, str(st))

    print("\n=== 10. 清理：恢复规则理由 ===")
    for one in {rec_id, keep_id} - {None}:
        st, r = call(f"/api/records/{one}/reason", "POST", {"use_llm": False, "force": True}, token=tk)
        check(f"记录 {one} 理由已还原为规则模板", r.get("ok") and r["data"]["source"] == "rule")

    ok = sum(1 for _, p, _ in results if p)
    print(f"\n{'='*52}\n结果：{ok}/{len(results)} 通过")
    failed = [n for n, p, _ in results if not p]
    if failed:
        print("失败项：")
        for n in failed:
            print("  -", n)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
