# -*- coding: utf-8 -*-
"""登录页（简化版）验收：标题登录/注册、记住密码、确认密码眼睛"""
import asyncio, json
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7862"
SHOT = "tools/shots"

async def main():
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)

        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.wait_for_timeout(500)

        t = await page.evaluate("""() => ({
            title: document.querySelector('#formTitle').textContent,
            sysLine: !!document.querySelector('#sysLine'),
            tabs: document.querySelectorAll('.tabs').length,
            formSub: !!document.querySelector('.form-sub'),
            demo: !!document.querySelector('#demoCard'),
            foot: !!document.querySelector('.form-foot'),
            brandCentered: (() => {
                const r = document.querySelector('.brand-row').getBoundingClientRect();
                const c = document.querySelector('.auth-card').getBoundingClientRect();
                return Math.abs((r.left + r.right)/2 - (c.left + c.right)/2) < 4;
            })(),
            eye2: !!document.querySelector('#eyeBtn2'),
            remember: !!document.querySelector('#rememberPwd'),
        })""")
        print("LOGIN_DOM:", json.dumps(t, ensure_ascii=False))
        await page.screenshot(path=f"{SHOT}/v2_login.png")

        # 确认密码眼睛
        await page.click('#switchBtn')  # 去注册
        await page.wait_for_timeout(300)
        reg = await page.evaluate("""() => ({
            title: document.querySelector('#formTitle').textContent,
            submit: document.querySelector('#submitText').textContent,
            alt: document.querySelector('#altText').textContent.trim()})""")
        print("REG:", json.dumps(reg, ensure_ascii=False))
        await page.click('#eyeBtn2')
        await page.wait_for_timeout(100)
        eye2 = await page.evaluate("() => document.querySelector('#password2').type")
        print("EYE2_TYPE:", eye2)
        await page.screenshot(path=f"{SHOT}/v2_register.png")

        # 登录 + 记住密码
        await page.evaluate("() => document.querySelector('#switchBtn').click()")
        await page.wait_for_timeout(200)
        await page.fill('#username', 'demo')
        await page.fill('#password', 'demo123')
        await page.check('#rememberPwd')
        await page.click('#submitBtn')
        await page.wait_for_selector('.form-note.ok', timeout=5000)
        await page.wait_for_url(f"{BASE}/", timeout=8000)
        print("LOGIN_OK")

        # 登出后回登录页，验证自动填入（不自动登录）
        await ctx.clear_cookies()
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.evaluate("() => { localStorage.removeItem('headhunter.token'); localStorage.removeItem('headhunter.user'); }")
        await page.goto(f"{BASE}/login", wait_until="networkidle")
        await page.wait_for_timeout(500)
        auto = await page.evaluate("""() => ({
            u: document.querySelector('#username').value,
            p: document.querySelector('#password').value,
            url: location.pathname,
            chk: document.querySelector('#rememberPwd').checked})""")
        print("AUTO_FILL:", json.dumps(auto))
        await page.screenshot(path=f"{SHOT}/v2_login_autofill.png")

        print("ERRORS:", json.dumps(errors))
        await browser.close()

asyncio.run(main())
