"""公司信息多源容错查询：本地缓存 -> 百度百科 -> 企查查API -> GLM 生成兜底。"""
import re

from core.llm import llm
from crawler import qcc_client
from crawler.baike_spider import BaikeSpider
from db import pool

_spider = BaikeSpider()


def normalize_name(name: str) -> str:
    """公司名归一化：去空格、去口语指代词。"""
    name = name.strip()
    name = re.sub(r"(这家|那家|公司的|这个|那个)", "", name)
    return name.strip()


def name_variants(name: str) -> list[str]:
    """生成百科查询的名称变体：全名 -> 去口语词 -> 去"公司"后缀。

    例："智谱这家公司" -> ["智谱这家公司", "智谱", "智谱公司"]
        "示例科技有限公司" -> ["示例科技有限公司", "示例科技有限公司", "示例科技"]
    """
    base = normalize_name(name)
    vs: list[str] = []
    for v in (base, re.sub(r"公司$", "", base)):
        v = v.strip()
        if len(v) >= 2 and v not in vs:
            vs.append(v)
    return vs or [name]


def _save_company(name: str, introduction: str, intro_source: int, industry: str | None = None):
    pool.execute(
        "INSERT INTO companies (name, industry, introduction, intro_source) "
        "VALUES (%s, %s, %s, %s) ON DUPLICATE KEY UPDATE "
        "introduction = VALUES(introduction), intro_source = VALUES(intro_source)",
        (name, industry, introduction, intro_source),
    )


def lookup_company(name: str) -> dict:
    """返回 {name, introduction, intro_source, source_label}。"""
    name = normalize_name(name)

    # 1. 本地缓存（按全名 + 变体依次查）
    for v in name_variants(name):
        rows = pool.query(
            "SELECT name, industry, introduction, intro_source FROM companies WHERE name = %s", (v,))
        if rows and rows[0]["introduction"]:
            r = rows[0]
            label = {0: "来自本地数据库（此前抓取自百度百科）",
                     1: "来自本地数据库（AI 生成，仅供参考）",
                     2: "来自本地数据库（企查查工商信息）"}.get(r["intro_source"],
                                                          "来自本地数据库")
            return {**r, "source_label": label}

    # 2. 百度百科：多名称变体依次尝试，命中即止
    for v in name_variants(name):
        result = _spider.fetch_company_intro(v)
        if result and result.get("introduction"):
            _save_company(v, result["introduction"], intro_source=0)
            return {"name": v, "introduction": result["introduction"], "intro_source": 0,
                    "source_label": "来自百度百科"}

    # 3. 企查查开放平台（需配置 Key；工商登记信息，适合百科未收录的小公司）
    if qcc_client.available():
        hit = qcc_client.search_company(name)
        if hit and hit.get("Name"):
            details = qcc_client.basic_details(hit["Name"])
            if details:
                intro = qcc_client.compose_introduction(details)
                if intro:
                    _save_company(hit["Name"], intro, intro_source=2,
                                  industry=details.get("EconKind"))
                    return {"name": hit["Name"], "introduction": intro, "intro_source": 2,
                            "source_label": "来自企查查开放平台（工商登记信息）"}

    # 4. GLM 兜底（用用户原话里的公司名，语义更完整）
    text = llm.chat(
        f"请用 200 字以内介绍公司「{name}」，包括主营业务与行业地位。"
        "如果不确定该公司是否存在，请明确说明，不要编造细节；"
        "并提醒用户可通过企查查/天眼查等平台人工核实。"
    )
    if text:
        _save_company(name, text, intro_source=1)
        return {"name": name, "introduction": text, "intro_source": 1,
                "source_label": "由 AI 生成（未命中权威数据源），建议在企查查人工核实"}

    return {"name": name, "introduction": None, "intro_source": -1,
            "source_label": "多源查询均失败"}
