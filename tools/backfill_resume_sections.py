"""一次性数据补全：邮箱/性别/学历缺失填充 + 生成教育背景、荣誉证书、工作经验。

运行: python tools/backfill_resume_sections.py
幂等：只更新缺失字段与三个新列（新列每次按候选人重算覆盖，保证内容与年限一致）。
"""
import json
import random
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
random.seed(42)

SCHOOLS = ["华中科技大学", "武汉大学", "武汉理工大学", "华中师范大学", "湖北大学", "中南财经政法大学", "中国地质大学"]
MAJORS = ["计算机科学与技术", "软件工程", "信息管理与信息系统", "人工智能", "数据科学与大数据技术", "电子信息工程", "工商管理"]
HONORS_POOL = [
    "CET-6", "全国大学生数学建模竞赛二等奖", "PMP 项目管理认证", "软考系统架构设计师",
    "校级一等奖学金", "优秀毕业生", "阿里云 ACP 认证", "全国职业院校技能大赛三等奖",
    "华为 HCIP 认证", "Kaggle 铜牌", " SCP 银牌", "校级三好学生",
]
COMPANIES = ["武汉云智科技", "长江数讯", "星河软件", "烽火信息", "光谷智能", "中南数科", "楚天数据"]
ROLES = ["软件工程师", "算法工程师", "数据分析师", "后端开发工程师", "项目经理", "测试工程师", "运维工程师"]
DESCS = [
    "参与核心模块设计与开发，主导两个迭代按期交付",
    "负责数据链路搭建与性能优化，接口耗时降低 40%",
    "负责推荐/匹配算法落地，上线后转化率提升 15%",
    "带领 3 人小组完成客户交付项目，获得客户年度好评",
    "搭建自动化测试流水线，回归测试耗时缩短一半",
]


def edu_entries(edu: str, grad_year: int) -> list:
    degree = {"大专": "大专", "本科": "本科", "硕士": "硕士", "博士": "博士"}.get(edu or "", "本科")
    span = {"博士": 5, "硕士": 3}.get(degree, 4)
    return [{
        "school": random.choice(SCHOOLS),
        "major": random.choice(MAJORS),
        "degree": degree,
        "period": f"{grad_year - span}-{grad_year}",
    }]


def work_entries(years: float) -> list:
    years = int(years or 0)
    n = 1 if years <= 3 else (2 if years <= 8 else 3)
    out, end = [], 2026
    for i in range(n):
        seg = max(1, round(years / n)) if years else 1
        start = end - seg
        out.append({
            "company": random.choice(COMPANIES),
            "role": random.choice(ROLES),
            "period": f"{start}-{end}" if i else f"{start}-至今",
            "desc": random.choice(DESCS),
        })
        end = start
    return out


def main():
    from db import pool
    rows = pool.query("SELECT id, name, gender, email, education, years_of_exp, data_source FROM candidates ORDER BY id")
    filled_mail = filled_gender = filled_edu = 0
    for r in rows:
        rid, years = r["id"], float(r["years_of_exp"] or 0)
        sets, params = [], []

        if not (r["email"] or "").strip():
            sets.append("email=%s")
            params.append(f"cand{rid}@example.com")
            filled_mail += 1
        if not (r["gender"] or "").strip():
            sets.append("gender=%s")
            params.append("男" if rid % 2 else "女")
            filled_gender += 1
        edu = (r["education"] or "").strip()
        if not edu or edu == "未填写":
            edu = "大专" if years < 3 else ("硕士" if years > 12 else "本科")
            sets.append("education=%s")
            params.append(edu)
            filled_edu += 1

        grad_year = max(2008, 2026 - int(years))
        sets.append("edu_background=%s")
        params.append(json.dumps(edu_entries(edu, grad_year), ensure_ascii=False))
        sets.append("honors=%s")
        params.append(json.dumps(random.sample(HONORS_POOL, k=min(3, 2 + rid % 2)), ensure_ascii=False))
        sets.append("work_experience=%s")
        params.append(json.dumps(work_entries(years), ensure_ascii=False))

        pool.execute(f"UPDATE candidates SET {', '.join(sets)} WHERE id=%s", (*params, rid))

    left = pool.query("SELECT COUNT(*) n FROM candidates WHERE email IS NULL OR email=''")[0]["n"]
    print(f"候选人 {len(rows)} 人：补邮箱 {filled_mail}、补性别 {filled_gender}、补学历 {filled_edu}；"
          f"三段简历栏目已全部生成；剩余缺邮箱 {left} 人。")


if __name__ == "__main__":
    main()
