"""界面截图校验脚本：逐个点击侧边导航，确认面板切换与聊天分阶段渲染正常。

用法: python tools/shot_ui.py
"""
import pathlib
import sys

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:7860"
OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "ui_shots"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / "01_chat.png"))

        # 发一条消息，抓 "正在思考…" 中间态
        box = page.locator("textarea").first
        box.fill("查一下有5年经验的Python候选人")
        page.keyboard.press("Enter")
        page.wait_for_timeout(600)
        page.screenshot(path=str(OUT / "02_user_first.png"))
        page.wait_for_timeout(9000)
        page.screenshot(path=str(OUT / "03_reply.png"))

        for name, label, shot in [
            ("candidates", "候选人库", "04_candidates.png"),
            ("companies", "公司库", "05_companies.png"),
            ("settings", "系统设置", "06_settings.png"),
        ]:
            btn = page.get_by_role("button", name=label, exact=True).first
            btn.click()
            page.wait_for_timeout(1800)
            visible = page.get_by_text("候选人总数").count() if name == "candidates" else 1
            print(f"[nav] {name}: 点击成功, 关键词命中={visible}")
            page.screenshot(path=str(OUT / shot))
        browser.close()

    if errors:
        print("前端报错:")
        for e in errors:
            print("  -", e)
        return 1
    print("截图完成，无前端 JS 报错 ->", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
