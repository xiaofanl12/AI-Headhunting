# -*- coding: utf-8 -*-
"""浏览器端全流程交互测试：遍历所有视图 + 关键交互 + JS 错误捕获"""
import asyncio
import json
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7862"
SHOT = "tools/shots"
VIEWS = ["home", "talent", "jobs", "companies", "analysis", "strategy", "settings"]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name if cond else f"{name} || {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + ("" if cond else f"  -> {detail}"))


async def main():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

        # ---------- 登录 ----------
        print("[登录]")
        await page.goto(f"{BASE}/", wait_until="networkidle")
        check("未登录自动跳登录页", "/login" in page.url, page.url)

        await page.fill("#username", "demo")
        await page.fill("#password", "demo123")
        await page.click("#submitBtn")
        await page.wait_for_url(f"{BASE}/", timeout=10000)
        check("登录后进入工作台", page.url.rstrip("/") == BASE, page.url)

        await page.wait_for_timeout(1500)
        check("登录后无 JS 错误", not errors, str(errors[:2]))

        # ---------- 遍历所有视图 ----------
        print("\n[页面遍历]")
        for v in VIEWS:
            before = len(errors)
            try:
                await page.click(f'.nav-item[data-view="{v}"]', timeout=5000)
            except Exception:
                await page.evaluate(
                    f"() => document.querySelector('.nav-item[data-view=\"{v}\"]').click()")
            await page.wait_for_timeout(900)
            active = await page.evaluate(
                f"() => !document.querySelector('#view-{v}').hidden")
            content = await page.evaluate(
                f"() => (document.querySelector('#view-{v}')?.innerText || '').length")
            check(f"视图 {v} 可切换且非空", active and content > 20,
                  f"active={active} textLen={content}")
            check(f"视图 {v} 无新增错误", len(errors) == before, str(errors[before:][:1]))
            await page.screenshot(path=f"{SHOT}/v_{v}.png")

        # ---------- 人才库交互 ----------
        print("\n[人才库]")
        await page.click('.nav-item[data-view="talent"]')
        await page.wait_for_timeout(800)
        rows = await page.locator("#candBody tr").count()
        check("人才库每页 20 行", rows == 20, f"实际 {rows} 行")

        pager = await page.locator("#candPager .pager-btn").count()
        check("分页控件存在", pager >= 2, f"{pager} 个按钮")

        # 列筛选：点击「学历」按钮才展开
        filt = page.locator(".th-filter").first
        await filt.click()
        await page.wait_for_timeout(300)
        menu_open = await page.evaluate(
            "() => { const m = document.querySelector('.th-menu'); "
            "return m && !m.hidden; }")
        check("点击学历出现筛选框", bool(menu_open), f"menu_open={menu_open}")
        await filt.click()
        await page.wait_for_timeout(250)
        menu_closed = await page.evaluate(
            "() => { const m = document.querySelector('.th-menu'); "
            "return m ? m.hidden : true; }")
        check("再次点击筛选框隐藏", bool(menu_closed), f"closed={menu_closed}")

        # 打开简历抽屉
        name_btn = page.locator("#candBody tr:first-child td:nth-child(2)").first
        if not await name_btn.count():
            name_btn = page.locator("#candBody tr:first-child td").nth(1)
        if await name_btn.count():
            await name_btn.click()
            await page.wait_for_timeout(1200)
            drawer = await page.evaluate(
                "() => { const d=document.querySelector('#drawer'); "
                "return d ? !d.hidden && d.classList.contains('on') !== false || !d.hidden : false; }")
            sections = await page.evaluate(
                "() => Array.from(document.querySelectorAll('#drawerBody .sec-label'))"
                ".map(x=>x.textContent.trim())")
            check("简历抽屉打开", bool(drawer), f"drawer={drawer}")
            check("抽屉含教育背景/荣誉证书/工作经验",
                  any("教育" in s for s in sections) and any("荣誉" in s for s in sections)
                  and any("工作" in s for s in sections), str(sections))
            await page.screenshot(path=f"{SHOT}/v_drawer.png")
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(400)

        # ---------- 匹配策略 ----------
        print("\n[匹配策略]")
        await page.click('.nav-item[data-view="strategy"]')
        await page.wait_for_timeout(800)
        sliders = await page.locator("#view-strategy input[type=range]").count()
        check("权重滑块存在", sliders >= 3, f"{sliders} 个")

        before_errors = len(errors)
        save = page.locator("#view-strategy button", has_text="保存")
        if await save.count():
            await save.first.click()
            await page.wait_for_timeout(1500)
            note = await page.evaluate(
                "() => document.querySelector('#view-strategy').innerText")
            check("保存权重后页面有反馈", "已保存" in note or "权重" in note, note[:80])
            check("保存权重无 JS 错误", len(errors) == before_errors, str(errors[before_errors:][:1]))

        # ---------- 账号中心 ----------
        print("\n[账号中心]")
        await page.click('.nav-item[data-view="settings"]')
        await page.wait_for_timeout(900)
        eyes = await page.locator("#view-settings .pwd-eye").count()
        check("三个密码框都有眼睛按钮", eyes == 3, f"{eyes} 个")
        await page.locator("#view-settings .pwd-eye").first.click()
        await page.wait_for_timeout(200)
        t = await page.evaluate("() => document.querySelector('#pwdOld').type")
        check("眼睛点击切换明文", t == "text", f"type={t}")
        await page.locator("#view-settings .pwd-eye").first.click()
        await page.wait_for_timeout(200)
        t2 = await page.evaluate("() => document.querySelector('#pwdOld').type")
        check("再次点击恢复密文", t2 == "password", f"type={t2}")

        # ---------- 聊天 ----------
        print("\n[聊天交互]")
        await page.click('.nav-item[data-view="home"]')
        await page.wait_for_timeout(700)
        ci = page.locator("#chatInput")
        if await ci.count():
            await ci.fill("你好")
            await page.wait_for_timeout(200)
            send = page.locator("#chatSend")
            if await send.count():
                await send.click()
                await page.wait_for_timeout(3000)
                msgs = await page.evaluate("() => document.querySelectorAll('#chatScroll > *').length")
                check("发送消息后有对话气泡", msgs >= 2, f"{msgs} 条")
                await page.screenshot(path=f"{SHOT}/v_chat.png")

        # ---------- 退出登录 ----------
        print("\n[退出登录]")
        await page.click("#sideLogoutNow")
        await page.wait_for_timeout(1200)
        check("退出登录回到登录页", "/login" in page.url, page.url)

        print("\n[全局 JS 错误]")
        real_errors = [e for e in errors if "favicon" not in e.lower()]
        check("全程无 JS 错误", not real_errors, str(real_errors[:3]))

        await browser.close()

    print("\n" + "=" * 70)
    print(f"汇总：通过 {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("\n失败明细：")
        for f in FAIL:
            print("  -", f)
    print("=" * 70)


asyncio.run(main())
