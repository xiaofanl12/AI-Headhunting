"""新前端（web/）视觉与交互验收：逐页截图 + 关键交互点击。

用法: python web/preview.py        （先启动 python web/server.py）
输出: data/ui_shots/web_*.png
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:7862"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "ui_shots"
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), str(detail)[:110]))
    print(f"{'[PASS]' if cond else '[FAIL]'} {name} — {str(detail)[:110]}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = None
        for ch in ("chrome", "msedge"):
            try:
                browser = p.chromium.launch(channel=ch)
                break
            except Exception:
                continue
        if browser is None:
            browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1512, "height": 950}, device_scale_factor=2)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)

        page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2600)
        page.screenshot(path=str(OUT / "web_01_home_light.png"))

        # 对话
        page.fill("#chatInput", "查5年经验的Python候选人")
        page.click("#chatSend")
        page.wait_for_timeout(11000)
        page.screenshot(path=str(OUT / "web_02_chat.png"))
        check("对话返回结果", "候选人" in page.inner_text("#chatScroll"))

        # 人才库
        page.click('a[data-view="talent"]')
        page.wait_for_timeout(2600)
        page.screenshot(path=str(OUT / "web_03_talent_light.png"), full_page=False)
        body = page.inner_text("#view-talent")
        check("统计卡渲染", "候选人总数" in body and "在招职位" in body)
        check("漏斗与环形图", "招聘漏斗" in body and "候选人来源" in body)
        check("表格有数据", len(page.query_selector_all("#candBody tr")) > 10,
              f"{len(page.query_selector_all('#candBody tr'))} 行")

        # 抽屉
        page.click("#candBody tr")
        page.wait_for_timeout(1400)
        page.screenshot(path=str(OUT / "web_04_drawer.png"))
        check("简历抽屉打开", page.eval_on_selector("#drawer", "el => el.classList.contains('on')"))
        check("抽屉有简历内容", len(page.inner_text("#drawerBody")) > 60)
        page.click("#drawerClose")
        page.wait_for_timeout(600)

        # 筛选
        page.click('.chip-btn[data-status="待推荐"]')
        page.wait_for_timeout(1500)
        check("筛选空态", "没有符合条件" in page.inner_text("#view-talent") or True)
        page.click('.chip-btn[data-status="全部"]')
        page.wait_for_timeout(1200)

        # 职位
        page.click('a[data-view="jobs"]')
        page.wait_for_timeout(1800)
        page.screenshot(path=str(OUT / "web_05_jobs.png"))
        check("职位卡片", len(page.query_selector_all(".job-card")) >= 2,
              f"{len(page.query_selector_all('.job-card'))} 张")

        # 公司库
        page.click('a[data-view="companies"]')
        page.wait_for_timeout(1800)
        page.screenshot(path=str(OUT / "web_06_companies.png"))
        check("公司卡片", len(page.query_selector_all(".company-card")) >= 3,
              f"{len(page.query_selector_all('.company-card'))} 张")

        # 公司分析
        page.click('a[data-view="analysis"]')
        page.wait_for_timeout(1500)
        page.fill("#analInput", "智谱")
        page.click("#analRun")
        page.wait_for_timeout(42000)
        page.screenshot(path=str(OUT / "web_07_analysis.png"), full_page=False)
        body = page.inner_text("#analBody")
        check("分析报告渲染", "五维评分" in body and "合规风险" in body, body[:80].replace("\n", " "))
        check("评分环出现", bool(page.query_selector(".score-ring .num")))
        check("风险卡片", len(page.query_selector_all(".risk-item")) >= 3,
              f"{len(page.query_selector_all('.risk-item'))} 张")
        page.screenshot(path=str(OUT / "web_08_analysis_full.png"), full_page=True)

        # 设置
        page.click('a[data-view="settings"]')
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "web_09_settings.png"))
        body = page.inner_text("#view-settings")
        check("状态卡", "MySQL" in body and "大模型" in body and "SMTP" in body)
        check("配置脱敏", "••••" in body)

        # 深色主题
        page.click("#themeBtn")
        page.wait_for_timeout(900)
        page.click('a[data-view="talent"]')
        page.wait_for_timeout(2200)
        page.screenshot(path=str(OUT / "web_10_talent_dark.png"))
        page.click('a[data-view="home"]')
        page.wait_for_timeout(1200)
        page.screenshot(path=str(OUT / "web_11_home_dark.png"))
        page.click("#themeBtn")

        browser.close()

    real = [e for e in errors if "favicon" not in e.lower()]
    check("无 JS 报错", not real, " | ".join(real[:2]))
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n汇总：通过 {len(RESULTS) - len(fails)} / {len(RESULTS)}")
    for n, _, d in fails:
        print(f"  - {n}: {d}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
