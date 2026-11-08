"""百度百科公司词条采集：开放词条 API 优先，HTML 页面兜底。

实测结论（2026-11）：
- PC/H5 页面 https://baike.baidu.com/item/xxx 对非浏览器请求返回 403 反爬；
- 开放词条 API BaikeLemmaCardApi 可正常返回 JSON（含 abstract 摘要），是主通道。
合规：限速 3s/请求、UA 标识、只取公开摘要。
"""
import time
from urllib.parse import quote

import requests

from db import pool

BAIKE_API = (
    "https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
    "?scope=103&format=json&appid=379020&bk_key={}"
)
BAIKE_PAGE = "https://baike.baidu.com/item/{}"


class BaikeSpider:
    RATE_LIMIT_SECONDS = 3
    TIMEOUT = 8
    RETRIES = 1
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://baike.baidu.com/",
    }
    _last_request_ts = 0.0
    _html_blocked = False  # HTML 通道熔断：一旦 403 就不再重试，避免每次白等 3 秒

    def _get(self, url: str) -> requests.Response | None:
        """限速 + 重试的 GET。"""
        for attempt in range(self.RETRIES + 1):
            elapsed = time.time() - BaikeSpider._last_request_ts
            if elapsed < self.RATE_LIMIT_SECONDS:
                time.sleep(self.RATE_LIMIT_SECONDS - elapsed)
            BaikeSpider._last_request_ts = time.time()
            try:
                resp = requests.get(url, headers=self.HEADERS, timeout=self.TIMEOUT)
                if resp.status_code == 200:
                    return resp
                if resp.status_code == 403 and "/item/" in url:
                    BaikeSpider._html_blocked = True
                print(f"[baike] HTTP {resp.status_code}: {url[:80]}")
            except requests.RequestException as e:
                print(f"[baike] 请求失败({attempt + 1}/{self.RETRIES + 1}): {e}")
        return None

    def _fetch_via_api(self, name: str) -> str | None:
        resp = self._get(BAIKE_API.format(quote(name)))
        if resp is None:
            return None
        try:
            data = resp.json()
        except ValueError:
            return None
        text = (data.get("abstract") or data.get("desc") or "").strip()
        return text[:2000] or None

    def _fetch_via_html(self, name: str) -> str | None:
        if BaikeSpider._html_blocked:
            return None
        resp = self._get(BAIKE_PAGE.format(quote(name)))
        if resp is None:
            return None
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "lxml")
        node = soup.select_one(".lemma-summary, .J-lemma-content, [class*='lemmaSummary']")
        return node.get_text(" ", strip=True)[:2000] if node else None

    def fetch_company_intro(self, company_name: str) -> dict | None:
        """抓取公司词条摘要。失败返回 None，由调用方走 GLM 兜底。"""
        start = time.time()
        intro, source = None, "baike_api"
        try:
            intro = self._fetch_via_api(company_name)
        except Exception as e:
            print(f"[baike] API 异常: {e}")
        if not intro:
            source = "baike_html"
            try:
                intro = self._fetch_via_html(company_name)
            except Exception as e:
                print(f"[baike] HTML 异常: {e}")

        status = "success" if intro else "failed"
        try:
            pool.execute(
                "INSERT INTO crawl_logs (url, source, status, rows_crawled, cost_ms) VALUES (%s,%s,%s,%s,%s)",
                (BAIKE_API.format(quote(company_name)) if source == "baike_api"
                 else BAIKE_PAGE.format(quote(company_name)),
                 source, status, 1 if intro else 0, int((time.time() - start) * 1000)),
            )
        except Exception as e:
            print(f"[baike] 写日志失败: {e}")

        return {"introduction": intro, "intro_source": 0} if intro else None
