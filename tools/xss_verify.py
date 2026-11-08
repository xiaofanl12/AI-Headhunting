# -*- coding: utf-8 -*-
"""XSS 浏览器实证：注入恶意职位名，确认前端不执行脚本"""
import asyncio
import json
import urllib.request
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:7862"


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if token:
        h["X-Auth-Token"] = token
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


async def main():
    tok = call("POST", "/api/auth/login",
               {"username": "demo", "password": "demo123"})["data"]["token"]
    payload = '<img src=x onerror="window.__XSS_HIT__=1"><script>window.__XSS_HIT__=2</script>'
    call("POST", "/api/positions",
         {"company": payload, "title": payload + "职位", "min_years": 0}, token=tok)

    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 1440, "height": 900})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        # 预置 token
        await pg.goto(f"{BASE}/login", wait_until="networkidle")
        await pg.evaluate(f"() => localStorage.setItem('headhunter.token', '{tok}')")
        await pg.goto(f"{BASE}/", wait_until="networkidle")
        await pg.wait_for_timeout(2000)

        hit = await pg.evaluate("() => window.__XSS_HIT__ || 0")
        print("XSS 执行标记:", hit, "(0 = 未执行，安全)")
        imgs = await pg.evaluate("() => document.images.length")
        print("DOM 里注入的 <img> 数量:", imgs)

        # 切到在招职位页确认渲染
        await pg.click('.nav-item[data-view="jobs"]')
        await pg.wait_for_timeout(1200)
        hit2 = await pg.evaluate("() => window.__XSS_HIT__ || 0")
        body_text = await pg.evaluate("() => document.body.innerText")
        print("职位页 XSS 标记:", hit2, "(0 = 安全)")
        print("页面是否原样显示脚本源码（说明已转义）:", "<script>" in body_text)
        await pg.screenshot(path="tools/shots/xss_check.png")

        # 清理
        pos = call("GET", "/api/positions", token=tok)["data"]
        ids = [x["id"] for x in pos if "<img" in str(x.get("title", ""))]
        for i in ids:
            try:
                import sys, os
                sys.path.insert(0, ".")
                from db import pool
                pool.execute("DELETE FROM positions WHERE id = %s", (i,))
            except Exception as e:
                print("cleanup err", e)
        print("已清理职位:", ids, "| JS 错误:", errs[:2])
        await b.close()


asyncio.run(main())
