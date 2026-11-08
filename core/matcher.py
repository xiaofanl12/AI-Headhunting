"""匹配引擎：确定性算法，可单测、可复现。LLM 只负责生成推荐理由。

总分 = 技能匹配分 × skill_weight + 经验评分 × (1 - skill_weight)，
达到 threshold 标记为「推荐」。权重可在界面由 HR 动态调整
（例如创业公司重潜力轻经验时把技能权重调高），持久化在 match_settings 表。

设计原则：算法分只用于「过滤 + 标记」，最终邀约由 HR 查阅详细项目经验后
人工决定（contact_records.hr_decision）。
"""
DEFAULT_SKILL_WEIGHT = 0.7
DEFAULT_THRESHOLD = 80
STRONG_THRESHOLD = 90  # 强烈推荐档（仍需 HR 人工确认邀约）

# 同义词归一表
SYNONYMS = {
    "js": "javascript",
    "ts": "typescript",
    "llm": "大模型",
    "nlp": "自然语言处理",
    "ml": "机器学习",
    "dl": "深度学习",
    "py": "python",
    "mysql": "sql",
}

# ---- 权重读写（DB 不可用时回退默认值，保证单测/离线可用）----
_weights_cache: dict | None = None


def normalize_skill(tag: str) -> str:
    tag = tag.strip().lower()
    return SYNONYMS.get(tag, tag)


def _load_db() -> dict | None:
    try:
        from db import pool
        rows = {r["skey"]: r["sval"] for r in pool.query("SELECT skey, sval FROM match_settings")}
        if "skill_weight" in rows:
            out = {"skill": float(rows["skill_weight"])}
            if "threshold" in rows:
                out["threshold"] = int(float(rows["threshold"]))
            return out
    except Exception:
        pass
    return None


def get_weights(force: bool = False) -> dict:
    """当前生效的权重 {skill, exp, threshold}；带进程内缓存。"""
    global _weights_cache
    if _weights_cache is None or force:
        db = _load_db() or {}
        skill = float(db.get("skill", DEFAULT_SKILL_WEIGHT))
        threshold = int(db.get("threshold", DEFAULT_THRESHOLD))
        _weights_cache = {"skill": skill, "exp": round(1 - skill, 4),
                          "threshold": threshold}
    return dict(_weights_cache)


def set_weights(skill_weight: float, threshold: int = DEFAULT_THRESHOLD) -> dict:
    """保存权重（0<skill<1，50<=threshold<=100），返回生效值。"""
    skill_weight = float(skill_weight)
    if not 0.0 < skill_weight < 1.0:
        raise ValueError("技能权重必须在 0~1 之间（不含端点）")
    threshold = int(threshold)
    if not 50 <= threshold <= 100:
        raise ValueError("推荐阈值必须在 50~100 之间")
    from db import pool
    pool.execute(
        "INSERT INTO match_settings (skey, sval) VALUES ('skill_weight', %s), ('threshold', %s) "
        "ON DUPLICATE KEY UPDATE sval = VALUES(sval)",
        (f"{skill_weight:.4f}", str(threshold)))
    global _weights_cache
    _weights_cache = {"skill": skill_weight, "exp": round(1 - skill_weight, 4),
                      "threshold": threshold}
    return dict(_weights_cache)


def grade(score: float, threshold: float | None = None) -> str:
    """分数档位：算法结果只是标记，不等于邀约。"""
    t = DEFAULT_THRESHOLD if threshold is None else threshold
    if score >= STRONG_THRESHOLD:
        return "强烈推荐"
    if score >= t:
        return "推荐"
    return "储备"


def skill_score(candidate_tags: list[str], position_tags: list[str]) -> float:
    """技能匹配分：命中数 / 职位要求标签数 × 100。职位无标签时给中性分 60。"""
    if not position_tags:
        return 60.0
    cand = {normalize_skill(t) for t in candidate_tags or []}
    required = {normalize_skill(t) for t in position_tags}
    if not required:
        return 60.0
    hit = len(cand & required)
    return round(hit / len(required) * 100, 2)


def exp_score(years_of_exp: float, min_years: int) -> float:
    """经验评分：达标满分；每差 1 年扣 25 分，下限 0；超出不加分。"""
    if years_of_exp >= min_years:
        return 100.0
    return max(0.0, 100.0 - 25.0 * (min_years - years_of_exp))


def match(candidate: dict, position: dict, weights: dict | None = None) -> dict:
    """输入候选人/职位记录（含 skill_tags, years_of_exp / min_years），返回评分明细。

    weights 不传时读取持久化配置（get_weights）。
    硬性门槛：职位 min_years > 0 且候选人经验不足时，无论技能分多高
    都直接标记「不通过」（passed=False），不进入推荐 —— 权重再怎么调也绕不过。
    """
    w = weights or get_weights()
    s = skill_score(candidate.get("skill_tags") or [], position.get("skill_tags") or [])
    years = float(candidate.get("years_of_exp") or 0)
    min_y = int(position.get("min_years") or 0)
    e = exp_score(years, min_y)
    total = round(s * w["skill"] + e * w["exp"], 2)
    hard_fail = min_y > 0 and years < min_y
    return {
        "total": total,
        "skill_score": s,
        "exp_score": e,
        "recommend": (not hard_fail) and total >= w["threshold"],
        "passed": not hard_fail,
        "hard_fail": hard_fail,
        "min_years": min_y,
        "years_of_exp": years,
        "grade": "不通过" if hard_fail else grade(total, w["threshold"]),
        "weights": dict(w),
        "hit_skills": sorted(
            {str(t).strip() for t in (candidate.get("skill_tags") or [])
             if normalize_skill(t) in {normalize_skill(x) for x in (position.get("skill_tags") or [])}},
            key=str.lower),
    }


def missing_skills(candidate: dict, position: dict) -> list[str]:
    """职位要求里候选人未命中的技能标签（用于理由中说明缺口）。"""
    have = {normalize_skill(t) for t in (candidate.get("skill_tags") or [])}
    need = {normalize_skill(t) for t in (position.get("skill_tags") or [])}
    return sorted(t for t in (position.get("skill_tags") or []) if normalize_skill(t) in (need - have))


def reason_facts(candidate: dict, position: dict, result: dict) -> dict:
    """把理由要用的结构化事实抽出来，规则模板与 LLM 提示词共用同一份数据。"""
    w = result.get("weights") or get_weights()
    return {
        "candidate": candidate.get("name") or "",
        "position": position.get("title") or "",
        "years": float(candidate.get("years_of_exp") or 0),
        "min_years": int(position.get("min_years") or 0),
        "education": candidate.get("education") or "",
        "hit_skills": list(result.get("hit_skills") or []),
        "missing_skills": missing_skills(candidate, position),
        "skill_score": result.get("skill_score"),
        "exp_score": result.get("exp_score"),
        "total": result.get("total"),
        "grade": result.get("grade") or "",
        "skill_weight": w["skill"],
        "exp_weight": w["exp"],
        "threshold": w["threshold"],
    }


def build_reason(candidate: dict, position: dict, result: dict, llm_text: str | None) -> str:
    """推荐理由：LLM 可用则用 LLM 润色，否则用规则模板兜底。

    两种产出的信息量要对齐：都写清「命中什么、差什么、分项分、档位」，
    避免出现「LLM 那句话很漂亮但不知道凭什么」或「模板理由干巴巴」的落差。
    """
    if result.get("hard_fail"):
        return (
            f"未通过硬性经验门槛：职位「{position.get('title')}」要求 ≥ {result['min_years']} 年经验，"
            f"候选人「{candidate.get('name')}」仅 {result['years_of_exp']} 年，即使技能分 "
            f"{result['skill_score']} 也不进入推荐。如 HR 认为该候选人值得破格考虑，"
            f"请查阅其详细项目经验后自行判断。"
        )
    if llm_text:
        return llm_text

    f = reason_facts(candidate, position, result)
    hits = "、".join(f["hit_skills"]) or "无直接命中标签"
    lack = f"；未覆盖：{'、'.join(f['missing_skills'])}" if f["missing_skills"] else "（职位要求技能全部覆盖）"
    edu = f"，{f['education']}" if f["education"] else ""
    return (
        f"候选人「{f['candidate']}」（{f['years']:g} 年经验{edu}）与职位「{f['position']}」"
        f"技能命中：{hits}{lack}；技能分 {f['skill_score']}，经验分 {f['exp_score']}，"
        f"按当前权重（技能 {f['skill_weight']:.0%} / 经验 {f['exp_weight']:.0%}）总分 {f['total']}，"
        f"档位「{f['grade']}」。"
        f"该结果为算法标记，邀约前请查阅候选人详细项目经验后人工确认。"
    )


def reason_prompt(candidate: dict, position: dict, result: dict) -> str:
    """给 LLM 的推荐理由指令。宁可留缺口也不许编造，见 sanitize_reason。"""
    f = reason_facts(candidate, position, result)
    return (
        "你是资深 HR 招聘顾问，请为下面这次内部推荐写一段「推选理由」，给用人部门看。\n\n"
        f"职位：{f['position']}（要求 ≥ {f['min_years']} 年经验，技能要求："
        f"{'、'.join(position.get('skill_tags') or []) or '未填写'}）\n"
        f"候选人：{f['candidate']}（{f['years']:g} 年经验，"
        f"{f['education'] or '学历未填写'}，技能标签："
        f"{'、'.join(candidate.get('skill_tags') or []) or '未填写'}）\n"
        f"算法明细：命中技能 {'、'.join(f['hit_skills']) or '无'}；"
        f"未覆盖 {'、'.join(f['missing_skills']) or '无'}；"
        f"技能分 {f['skill_score']}，经验分 {f['exp_score']}，总分 {f['total']}，档位 {f['grade']}\n\n"
        "要求：\n"
        "1. 120-180 字，分 2-3 句，第一句点明为什么推，第二句说清岗位要求与他/她的对应关系；\n"
        "2. 只能用上面给出的技能标签、年限、学历作为依据，**不得编造项目经历、公司名、业绩数字**；\n"
        "3. 若有未覆盖的技能，要用一句话说明这是需要面试重点确认的点；\n"
        "4. 结尾提醒「算法结论仅供参考，请结合简历面谈确认」；\n"
        "5. 直接输出正文，不要写「推选理由：」之类的前缀，不要用 Markdown 标题和列表。"
    )


def sanitize_reason(text: str | None) -> str | None:
    """清洗 LLM 理由：去前缀/加粗符号，超长截断，空则返回 None 让调用方回退模板。"""
    if not text:
        return None
    t = text.strip()
    t = t.lstrip("*#> ")
    # 去掉「推选理由：」「推荐理由：」这类前缀
    for prefix in ("推选理由：", "推选理由:", "推荐理由：", "推荐理由:"):
        if t.startswith(prefix):
            t = t[len(prefix):].lstrip()
    t = t.replace("**", "").replace("##", "#").strip()
    if not t:
        return None
    # 单行理由用于决策台展示，过长的段落压成一行；600 字足够覆盖要求
    return " ".join(t.split())[:600]
