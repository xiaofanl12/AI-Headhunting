"""推送弹窗 UI 验收：决策台理由列渲染 + 打开弹窗 + 生成理由 + 关闭。

只做界面操作，不点「确认推送」（避免真的发信）。
"""
import asyncio
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 7874
BASE = f"http://127.0.0.1:{PORT}"
SHOTS = os.path.join(ROOT, "data", "ui_shots")
results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"  -> {detail}" if detail else ""))


def start_server():
    code = (f"import sys; sys.path.insert(0, {ROOT!r}); sys.path.insert(0, {os.path.join(ROOT, 'web')!r});"
            f"import uvicorn; from web.server import api;"
            f"uvicorn.run(api, host='127.0.0.1', port={PORT}, log_level='warning')")
    p = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(50):
        time.sleep(0.6)
        try:
            with opener.open(BASE + "/login", timeout=3) as r:
                if r.status == 200:
                    return p
        except Exception:
            if p.poll() is not None:
                print(p.stdout.read().decode()[:800])
                return None
    return p


async def main():
    from playwright.async_api import async_playwright
    os.makedirs(SHOTS, exist_ok=True)
    pwd = os.getenv("TEST_PWD", "demo123")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1560, "height": 1000})
        errors, console_errs = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: console_errs.append(m.text) if m.type == "error" else None)
        page.on("dialog", lambda d: asyncio.ensure_future(d.dismiss()))

        await page.goto(f"{BASE}/login", wait_until="domcontentloaded")
        await page.fill("#username", "demo")
        await page.fill("#password", pwd)
        await page.click("#submitBtn")
        await page.wait_for_url(f"{BASE}/", timeout=20000)
        check("登录并进入主界面", True)

        # 切到匹配策略（决策台所在页）
        await page.click("a.nav-item[data-view='strategy']")
        await page.wait_for_timeout(3000)

        rows = await page.locator("#decBody tr").count()
        check("决策台有记录行", rows > 0, f"{rows} 行")

        # 表头含「推选理由」
        head = await page.locator(".dec-table thead").inner_text()
        check("表头含推选理由列", "推选理由" in head, head.replace("\n", " | ")[:80])

        # 理由文本与来源标签
        reason_txt = await page.locator("#decBody tr").first.locator(".reason-text").inner_text()
        check("首行渲染出理由文本", len(reason_txt.strip()) > 10, reason_txt[:50])
        src = await page.locator("#decBody tr").first.locator(".src-tag").inner_text()
        check("首行显示理由来源标签", src.strip() != "", src)

        # 点理由展开
        await page.locator("#decBody tr").first.locator(".reason-text").click()
        await page.wait_for_timeout(300)
        opened = await page.locator("#decBody tr").first.locator(".reason-text.open").count()
        check("点击理由可展开全文", opened == 1)

        await page.screenshot(path=os.path.join(SHOTS, "push_01_decision_table.png"), full_page=False)

        # 打开推送弹窗
        push_btn = page.locator("#decBody [data-push]:not([disabled])").first
        check("存在可用的推送按钮", await push_btn.count() > 0)
        await push_btn.click()
        await page.wait_for_timeout(400)
        check("推送弹窗已打开", await page.locator("#pushMask").is_visible())

        # 邮件草稿要调一次模型（理由 + 邀约正文一次产出），实测 20-45 秒，
        # 故轮询等待，超时给到 120 秒——网络慢时不至于误判成失败
        async def wait_mail(timeout=120):
            for _ in range(int(timeout / 1.5)):
                s = await page.locator("#pushSubject").inner_text()
                if s and "生成中" not in s:
                    return s
                await page.wait_for_timeout(1500)
            return await page.locator("#pushSubject").inner_text()

        subj = await wait_mail()

        title = await page.locator("#pushTitle").inner_text()
        sub = await page.locator("#pushSub").inner_text()
        check("弹窗标题正确", title.strip() == "推送候选人", title)
        check("弹窗副标题含候选人与职位", "→" in sub, sub)

        rv = await page.input_value("#pushReason")
        check("理由输入框已带出理由", len(rv.strip()) > 10, rv[:50])

        check("邮件主题已生成", subj and "生成中" not in subj and "失败" not in subj, subj)
        body = await page.locator("#pushMailBody").inner_text()
        check("邮件正文已生成", len(body.strip()) > 20, body[:60])
        to = await page.locator("#pushTo").inner_text()
        check("收件人已显示", "@" in to, to)
        check("邮件正文不含内部评估信息",
              not any(k in body for k in ("算法分", "档位", "权重", "经验分", "技能分")),
              body[:80])
        check("邮件正文不含 LLM 自我说明",
              not any(k in body for k in ("正文约", "未提及", "仅以简历")), body[-60:])

        await page.screenshot(path=os.path.join(SHOTS, "push_02_modal.png"), full_page=False)

        # 用规则模板重生成理由
        await page.click("#pushGenRule")
        await page.wait_for_timeout(4000)
        rv2 = await page.input_value("#pushReason")
        check("规则模板可重新生成理由", len(rv2.strip()) > 10, rv2[:60])
        src2 = await page.locator("#pushReasonSrc").inner_text()
        check("理由来源显示规则模板", src2.strip() == "规则模板", src2)

        # 关闭弹窗
        await page.click("#pushCancel")
        await page.wait_for_timeout(500)
        check("弹窗已关闭", not await page.locator("#pushMask").is_visible())

        check("无 JS 运行时错误", len(errors) == 0, "; ".join(errors[:3]))
        check("无 console error", len(console_errs) == 0, "; ".join(console_errs[:3]))

        await browser.close()

    ok = sum(1 for _, p, _ in results if p)
    print(f"\n{'='*52}\n结果：{ok}/{len(results)} 通过")
    failed = [n for n, p, _ in results if not p]
    for n in failed:
        print("  失败：", n)
    return 0 if not failed else 1


if __name__ == "__main__":
    srv = start_server()
    if not srv:
        sys.exit(1)
    try:
        sys.exit(asyncio.run(main()))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=8)
        except Exception:
            srv.kill()
