"""公开数据集清洗导入：把简历/招聘 CSV 转成业务表种子数据。

用法:
    python -m crawler.dataset_import data/candidates.csv data/positions.csv

CSV 期望列:
    candidates.csv: name, phone, email, resume_text, skill_tags(分号分隔), years_of_exp, education
    positions.csv:  title, company, industry, skill_tags(分号分隔), min_years, salary_range

真实数据集建议（手动下载后放到 data/ 目录，脚本不代爬招聘平台）:
    - Kaggle: Resume Dataset / HR Analytics
    - 天池: 招聘画像相关公开数据集
"""
import csv
import json
import sys

from db import pool

SKILL_KEYWORDS = [
    "Python", "Java", "JavaScript", "TypeScript", "SQL", "MySQL", "Redis",
    "LangChain", "RAG", "机器学习", "深度学习", "大模型", "NLP", "自然语言处理",
    "Docker", "Kubernetes", "Spring", "Vue", "React", "Go", "C++",
]


def extract_skills(text: str) -> list[str]:
    """关键词词典匹配抽取技能标签（比 LLM 快且免费，用于批量导入）。"""
    lower = text.lower()
    return [kw for kw in SKILL_KEYWORDS if kw.lower() in lower]


def import_candidates(path: str) -> int:
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            tags = [t.strip() for t in (row.get("skill_tags") or "").split(";") if t.strip()]
            if not tags:
                tags = extract_skills(row.get("resume_text") or "")
            pool.execute(
                "INSERT INTO candidates (name, phone, email, resume_text, skill_tags, years_of_exp, education) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (row["name"], row.get("phone"), row.get("email"), row.get("resume_text"),
                 json.dumps(tags, ensure_ascii=False), row.get("years_of_exp") or 0,
                 row.get("education")),
            )
            n += 1
    print(f"candidates 导入完成: {n} 条")
    return n


def import_positions(path: str) -> int:
    n = 0
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            company = row["company"]
            pool.execute(
                "INSERT INTO companies (name, industry) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)",
                (company, row.get("industry")),
            )
            comp = pool.query("SELECT id FROM companies WHERE name = %s", (company,))[0]
            tags = [t.strip() for t in (row.get("skill_tags") or "").split(";") if t.strip()]
            pool.execute(
                "INSERT INTO positions (company_id, title, skill_tags, min_years, salary_range) "
                "VALUES (%s,%s,%s,%s,%s)",
                (comp["id"], row["title"], json.dumps(tags, ensure_ascii=False),
                 row.get("min_years") or 0, row.get("salary_range")),
            )
            n += 1
    print(f"positions 导入完成: {n} 条")
    return n


if __name__ == "__main__":
    if len(sys.argv) >= 2:
        import_candidates(sys.argv[1])
    if len(sys.argv) >= 3:
        import_positions(sys.argv[2])
