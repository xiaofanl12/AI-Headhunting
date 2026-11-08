"""浏览器级点击测试：真实打开页面，逐个面板/标签页/按钮点一遍。

覆盖：对话问答 → 候选人库（看板/筛选/导出）→ 在招职位（新增/关闭）
     → 公司库（背调入库）→ 公司分析（开始分析）→ 系统设置（自检）

用法: python tools/ui_click_test.py
截图输出: data/ui_shots/
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:7860"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "ui_shots"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)[:120]))
    print(f"{'[PASS]' if cond else '[FAIL]'} {name} — {str(detail)[:120]}")
    return cond


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        # 用本机已装的浏览器，免去 playwright install 下载内核
        browser = None
        for channel in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=channel)
                break
            except Exception:
                continue
        if browser is None:
            browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1000})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)

        # Gradio 会常驻 websocket，networkidle 永远等不到，改等首屏元素
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("text=导航", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "c01_chat.png"))
        check("页面加载", "AI 猎头" in page.content())

        # ---------- 对话 ----------
        box = page.locator("textarea").first
        box.fill("查5年经验的Python候选人")
        page.keyboard.press("Enter")
        page.wait_for_timeout(12000)
        page.screenshot(path=str(OUT / "c02_chat_reply.png"))
        body = page.inner_text("body")
        check("对话返回候选人", "共找到" in body or "工作年限" in body, body[-120:].replace("\n", " "))

        # ---------- 候选人库 ----------
        page.get_by_role("button", name="候选人库", exact=True).first.click()
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "c03_candidates.png"))
        body = page.inner_text("body")
        check("看板渲染", "候选人" in body and "在招职位" in body and "合作公司" in body)
        check("漏斗与转化率", "招聘漏斗" in body and "推送→触达" in body)
        check("候选人表格有数据", "待筛选" in body or "已推荐" in body)

        # 状态筛选（下拉改值后表格刷新）
        try:
            page.get_by_label("按状态筛选").click(force=True)
            page.wait_for_timeout(600)
            page.get_by_role("option", name="已推荐").first.click()
            page.wait_for_timeout(2000)
            page.screenshot(path=str(OUT / "c04_filter.png"))
            check("状态筛选可用", "已推荐" in page.inner_text("body"))
        except Exception as e:
            check("状态筛选可用", False, f"{type(e).__name__}: {e}")

        # 导出 CSV
        try:
            page.get_by_role("button", name="导出 CSV").first.click()
            page.wait_for_timeout(3000)
            page.screenshot(path=str(OUT / "c05_export.png"))
            check("导出 CSV", "已导出" in page.inner_text("body"),
                  page.inner_text("body")[-100:].replace("\n", " "))
        except Exception as e:
            check("导出 CSV", False, f"{type(e).__name__}: {e}")

        # ---------- 在招职位 ----------
        try:
            page.get_by_role("tab", name="在招职位").first.click()
            page.wait_for_timeout(1500)
            page.screenshot(path=str(OUT / "c06_positions.png"))
            body = page.inner_text("body")
            check("职位列表渲染", "技能标签" in body or "招聘中" in body)

            page.get_by_label("职位名称").fill("点击测试岗位")
            page.get_by_label("薪资区间").fill("20-30K")
            page.get_by_label("技能标签").fill("Python, Redis")
            page.get_by_role("button", name="新增", exact=True).first.click()
            page.wait_for_timeout(2500)
            page.screenshot(path=str(OUT / "c07_add_position.png"))
            check("新增职位", "已新增职位" in page.inner_text("body"),
                  page.inner_text("body")[-100:].replace("\n", " "))
        except Exception as e:
            check("新增职位", False, f"{type(e).__name__}: {e}")

        # ---------- 公司库 ----------
        page.get_by_role("button", name="公司库", exact=True).first.click()
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "c08_companies.png"))
        check("公司库表格", "简介长度" in page.inner_text("body"))
        try:
            page.get_by_label("公司名称").fill("智谱")
            page.get_by_role("button", name="背调并入库").first.click()
            page.wait_for_timeout(8000)
            page.screenshot(path=str(OUT / "c09_lookup.png"))
            check("背调并入库", "智谱" in page.inner_text("body"))
        except Exception as e:
            check("背调并入库", False, f"{type(e).__name__}: {e}")

        # ---------- 公司分析 ----------
        page.get_by_role("button", name="公司分析", exact=True).first.click()
        page.wait_for_timeout(2000)
        try:
            page.get_by_role("button", name="开始分析").first.click()
            page.wait_for_timeout(45000)
            page.screenshot(path=str(OUT / "c10_analysis.png"), full_page=True)
            body = page.inner_text("body")
            check("公司分析出报告", ("评分" in body) or ("合规风险" in body), body[-150:].replace("\n", " "))
        except Exception as e:
            check("公司分析出报告", False, f"{type(e).__name__}: {e}")

        # ---------- 系统设置 ----------
        page.get_by_role("button", name="系统设置", exact=True).first.click()
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "c11_settings.png"))
        body = page.inner_text("body")
        check("自检三项", "MySQL 连接正常" in body and "大模型" in body and "SMTP" in body)
        check("配置脱敏", "••••" in body or "未配置" in body)

        browser.close()

    real_errors = [e for e in errors if "favicon" not in e.lower()]
    check("无前端 JS 报错", not real_errors, " | ".join(real_errors[:3]))

    fails = [r for r in RESULTS if not r[1]]
    print(f"\n汇总：通过 {len(RESULTS) - len(fails)} / {len(RESULTS)}")
    for n, _, d in fails:
        print(f"  - {n}: {d}")
    print("截图目录:", OUT)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
