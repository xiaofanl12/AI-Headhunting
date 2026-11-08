"""真实中文简历语料导入：把公开研究语料转成 candidates 表数据。

数据来源
    ResumeNER（ACL 2018 "Chinese NER Using Lattice LSTM" 所用简历语料）
    镜像：https://gitee.com/terry-gjt/ner  ->  ResumeNER/{train,dev}.char.bmes
    性质：公开学术语料，用于信息抽取研究；文本为真实简历片段，姓名等实体来自原语料。

合规说明
    - 仅用于技术验证/演示，不做商业分发，不再二次传播原始语料。
    - 语料中不含（或已脱敏）联系方式，缺失的邮箱/手机号一律留空，
      由此产生的"脏数据"正好用于验证系统的容错能力（不应编造填充）。

用法
    python -m crawler.resume_corpus_import --limit 200          # 下载+解析+写快照
    python -m crawler.resume_corpus_import --limit 200 --import # 同时导入数据库
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict

import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

BASE = "https://gitee.com/terry-gjt/ner/raw/master/ResumeNER"
FILES = ("dev.char.bmes", "train.char.bmes")
SNAPSHOT = os.path.join(os.path.dirname(__file__), "..", "data", "real_candidates.json")

SKILL_KEYWORDS = [
    "Python", "Java", "JavaScript", "TypeScript", "SQL", "MySQL", "Oracle", "Redis",
    "Linux", "Docker", "Kubernetes", "Spring", "Vue", "React", "Go", "C++", "C#",
    "机器学习", "深度学习", "大模型", "NLP", "自然语言处理", "数据分析", "算法",
    "销售", "市场", "财务", "会计", "人力资源", "项目管理", "机械", "电气", "通信",
]

EDU_ORDER = ["博士", "硕士", "MBA", "本科", "大专", "中专", "高中"]

# 职务实体 -> 能力标签（该语料多为管理者/工程师经历，技能词少，用职务补充）
TITLE_SKILL_MAP = {
    "总经理": "管理", "董事": "管理", "厂长": "管理", "部长": "管理", "处长": "管理",
    "工程师": "工程技术", "技术": "工程技术", "研究员": "研究", "教授": "教育",
    "销售": "销售", "市场": "市场", "财务": "财务", "会计": "财务",
    "人力资源": "人力资源", "人事": "人力资源", "项目经理": "项目管理",
}


def download(dest_dir: str) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0 Safari/537.36"}
    paths = []
    for name in FILES:
        path = os.path.join(dest_dir, name)
        if os.path.exists(path) and os.path.getsize(path) > 1000:
            print(f"[缓存] {name} 已存在")
            paths.append(path)
            continue
        resp = requests.get(f"{BASE}/{name}", headers=headers, timeout=60)
        resp.raise_for_status()
        with open(path, "wb") as f:
            f.write(resp.content)
        print(f"[下载] {name} {len(resp.content)} bytes")
        time.sleep(3)  # 礼貌间隔
        paths.append(path)
    return paths


def parse_chunk(chunk: str) -> tuple[str, dict]:
    """解析 "字 TAG" 序列，返回 (纯文本, 实体字典)。"""
    chars, entities, buf = [], defaultdict(list), {}
    for line in chunk.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ch, tag = parts[0], parts[-1]
        chars.append(ch)
        if "-" not in tag:
            continue
        prefix, etype = tag.split("-", 1)
        if prefix == "B":
            buf[etype] = ch
        elif prefix == "M":
            buf[etype] = buf.get(etype, "") + ch
        elif prefix == "E":
            buf[etype] = buf.get(etype, "") + ch
            entities[etype].append(buf.pop(etype))
    return "".join(chars), entities


def extract_years(text: str, birth_year: int | None) -> float:
    """工作年限估算，优先级：显式"X年经验" > 最早工作年份 > 出生年推算 > 中性值 3。"""
    m = re.search(r"(\d{1,2})\s*年(?:以上)?(?:的)?(?:工作)?经[验历]", text)
    if m:
        return float(m.group(1))

    years_mentioned = [int(y) for y in re.findall(r"(19[5-9]\d|20[0-2]\d)\s*年", text)]
    # 只采用"成年之后"的年份，排除出生年
    work_years = [y for y in years_mentioned if not birth_year or y >= birth_year + 18]
    if work_years and min(work_years) < 2026:
        return float(max(1, min(2026 - min(work_years), 40)))

    if birth_year:
        return float(max(0, min(2026 - birth_year - 22, 40)))
    return 3.0


def extract_gender(text: str) -> str | None:
    """从简历文本推断性别：先生/女士/小姐 > 顿号标注 > 「性别：」字段。"""
    if re.search(r"先生", text):
        return "男"
    if re.search(r"女士|小姐", text):
        return "女"
    if re.search(r"[，,、]\s*男\s*[，,、]|性别\s*[:：]\s*男", text):
        return "男"
    if re.search(r"[，,、]\s*女\s*[，,、]|性别\s*[:：]\s*女", text):
        return "女"
    return None


def build_records(paths: list[str], limit: int) -> list[dict]:
    """按"姓名出现"为边界，把句子级片段合并成完整简历文本。

    该语料是 NER 任务的句子级标注（中位数仅 25 字），直接一条当一份简历会
    全部因过短被丢弃。这里以 NAME 实体为分界：遇到带姓名的片段开新记录，
    后续片段持续并入，直到下一个姓名片段。
    """
    records, seen = [], set()
    for path in paths:
        raw = open(path, encoding="utf-8", errors="ignore").read()
        current = None  # {"text": [...], "ents": {...}, "name": str}
        for chunk in re.split(r"\n\s*\n", raw):
            if not chunk.strip():
                continue
            text, ents = parse_chunk(chunk)
            text = re.sub(r"\s+", "", text).strip()
            if not text:
                continue
            name = (ents.get("NAME") or [""])[0]
            if name and 1 < len(name) <= 4:
                if current:
                    records.append(current)
                current = {"chunks": [text], "ents": ents, "name": name}
            elif current:
                current["chunks"].append(text)
                for k, v in ents.items():
                    current["ents"].setdefault(k, []).extend(v)
                if len("".join(current["chunks"])) > 800:  # 超长则截断，避免串到无关内容
                    records.append(current)
                    current = None
        if current:
            records.append(current)

    out = []
    for r in records:
        text = "".join(r["chunks"])[:2000]
        if len(text) < 80:
            continue
        key = hashlib.md5(f"{r['name']}{text[:80]}".encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        ents = r["ents"]
        edu = next((e for e in EDU_ORDER if e in text or any(e in x for x in ents.get("EDU", []))), None)
        birth = re.search(r"(19[5-9]\d|200\d)\s*年(?:\d{1,2}\s*月)?(?:出)?生", text)
        years = extract_years(text, int(birth.group(1)) if birth else None)
        skills = [kw for kw in SKILL_KEYWORDS if kw.lower() in text.lower()]
        title_text = " ".join(ents.get("TITLE", []) + ents.get("PRO", []))
        skills += [tag for kw, tag in TITLE_SKILL_MAP.items() if kw in title_text and tag not in skills]
        skills = list(dict.fromkeys(skills))  # 去重且保序

        email = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        phone = re.search(r"1[3-9]\d{9}", text)

        out.append({
            "name": r["name"],
            "gender": extract_gender(text),
            "email": email.group(0) if email else None,      # 缺失即留空，不编造
            "phone": phone.group(0) if phone else None,
            "resume_text": text,
            "skill_tags": skills[:8],
            "years_of_exp": years,
            "education": edu,
            "source_file": os.path.basename(path),
        })
        if len(out) >= limit:
            break
    return out


def save_snapshot(records: list[dict]):
    os.makedirs(os.path.dirname(SNAPSHOT), exist_ok=True)
    with open(SNAPSHOT, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    print(f"[快照] 写入 {SNAPSHOT}（{len(records)} 条）")


def import_to_db(records: list[dict]):
    from db import pool
    inserted = 0
    for r in records:
        exists = pool.query("SELECT id FROM candidates WHERE name = %s AND LEFT(resume_text, 40) = %s",
                            (r["name"], r["resume_text"][:40]))
        if exists:
            continue
        pool.execute(
            "INSERT INTO candidates (name, gender, phone, email, resume_text, skill_tags, years_of_exp, education, data_source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'resume_ner')",
            (r["name"], r.get("gender"), r["phone"], r["email"], r["resume_text"],
             json.dumps(r["skill_tags"], ensure_ascii=False), r["years_of_exp"], r["education"]),
        )
        inserted += 1
    print(f"[入库] 新增 {inserted} 条候选人（跳过重复 {len(records) - inserted} 条）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--import", dest="do_import", action="store_true")
    parser.add_argument("--offline", action="store_true", help="只用本地快照，不下载")
    args = parser.parse_args()

    if args.offline:
        records = json.load(open(SNAPSHOT, encoding="utf-8"))
        print(f"[离线] 从快照加载 {len(records)} 条")
    else:
        paths = download(os.path.join(os.path.dirname(__file__), "..", "data", "corpus"))
        records = build_records(paths, args.limit)
        save_snapshot(records)
        print(f"[解析] 得到 {len(records)} 条候选人")

    # 数据质量概览（体现真实数据的脏乱程度）
    n = len(records)
    with_email = sum(1 for r in records if r["email"])
    with_edu = sum(1 for r in records if r["education"])
    with_skill = sum(1 for r in records if r["skill_tags"])
    avg_skills = sum(len(r["skill_tags"]) for r in records) / max(n, 1)
    print(f"[质量] 有邮箱 {with_email}/{n}｜有学历 {with_edu}/{n}｜有技能标签 {with_skill}/{n}"
          f"｜人均标签 {avg_skills:.1f}")

    if args.do_import:
        import_to_db(records)


if __name__ == "__main__":
    main()
