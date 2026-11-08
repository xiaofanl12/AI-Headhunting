"""公司价值分析：评估「这家公司值不值得把候选人推过去」。

设计原则
--------
1. 打分只用库里能核对的事实（在招职位、薪资区间、推荐进展、简介来源、信息完整度），
   同样一份数据重复跑分数一致，猎头复盘时能对得上账；
2. 优势 / 风险 / 建议交给大模型写，但提示词强制「只能基于给定事实，不确定的写待核实」，
   避免把融资、营收、人数这类没数据支撑的内容写成结论；
3. 模型不可用时退化为规则版结论，缺 Key 不影响功能。

对外接口：analyze_company(name) -> dict，render_markdown(result) -> str
"""
import json
import re

from core import risk_monitor
from core.company_lookup import lookup_company
from core.llm import llm
from db import pool

SOURCE_LABELS = {0: "百度百科", 1: "AI 生成（未核实）", 2: "企查查工商信息"}

# 风险扣分规则：命中即扣，封顶 25 分（避免单靠扣分把总分压成 0，失去区分度）
RISK_PENALTY = {"shixin": 15, "exception": 10, "penalty": 5}
RISK_PENALTY_CAP = 25

# 每档的分数线与结论
LEVELS = (
    (75, "重点合作", "基础信息与推进数据都比较扎实，可以主动向候选人推荐"),
    (60, "可以推进", "基本条件成立，补齐 1-2 项关键信息后即可推进"),
    (45, "谨慎评估", "存在明显短板，建议先答疑再决定是否投入时间"),
    (0, "暂缓推荐", "关键信息缺失或推进停滞，现在推人容易砸口碑"),
)
_LEVEL_ORDER = [lv for _, lv, _ in LEVELS]  # 从高到低，用于降档比较


# --------------------------------------------------------------------------- #
# 事实采集
# --------------------------------------------------------------------------- #
_SALARY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:[-~—至到]\s*(\d+(?:\.\d+)?))?\s*([kK千万])")


def _parse_salary(text: str | None) -> tuple[float | None, float | None]:
    """从 "30-50K" / "30-60万/年" / "年薪 60 万" 里取出月薪区间（单位 K）。

    万是年薪口径，除以 12 换算成月薪，便于和 K 口径的职位一起比较。
    """
    if not text:
        return None, None
    vals = []
    for m in _SALARY_RE.finditer(str(text)):
        unit = m.group(3)
        factor = 1.0 if unit in ("k", "K", "千") else 10 / 12
        for g in (m.group(1), m.group(2)):
            if g:
                vals.append(float(g) * factor)
    if not vals:
        return None, None
    return min(vals), max(vals)


def _load_company(name: str) -> dict:
    rows = pool.query(
        "SELECT id, name, industry, scale, introduction, intro_source, created_at "
        "FROM companies WHERE name = %s", (name,))
    if rows:
        return rows[0]
    # 库里没有：先走多源背调（会自动入库），再取一次
    info = lookup_company(name)
    rows = pool.query(
        "SELECT id, name, industry, scale, introduction, intro_source, created_at "
        "FROM companies WHERE name = %s", (info.get("name") or name,))
    if rows:
        return rows[0]
    return {"id": None, "name": info.get("name") or name, "industry": None, "scale": None,
            "introduction": info.get("introduction"), "intro_source": info.get("intro_source")}


_SUFFIX_RE = re.compile(r"(有限责任公司|股份有限公司|有限公司|集团公司|公司|集团|"
                        r"科技|技术|网络|信息|智能|AI|Inc\.?|Ltd\.?)$", re.I)


def _core_name(name: str) -> str:
    """抽出公司名的核心词，用于同一家公司的多条目归并（智谱AI / 智谱 -> 智谱）。"""
    s = re.sub(r"[\s（）()·.,，]", "", str(name or ""))
    for _ in range(3):  # 反复剥离后缀：XX科技有限公司 -> XX科技 -> XX
        new = _SUFFIX_RE.sub("", s)
        if new == s:
            break
        s = new
    return s


def _company_group(name: str) -> tuple[list[dict], dict]:
    """把同一家公司的多条记录归到一组，返回 (全部记录, 主记录)。

    真实库里常有「智谱」「智谱AI」「北京智谱华章科技有限公司」多条，职位和简介
    分散在不同行，直接按名字单行查会把在招职位算漏，评分就会失真。
    """
    target = _load_company(name)
    try:
        rows = pool.query("SELECT id, name, industry, scale, introduction, intro_source, "
                          "created_at FROM companies")
    except Exception:
        return [target], target

    core = _core_name(target["name"])
    group = [target]
    if len(core) >= 2:
        for r in rows:
            if r["id"] == target["id"]:
                continue
            other = _core_name(r["name"])
            if not other:
                continue
            if core in other or other in core:
                group.append(r)

    ids = [r["id"] for r in group if r.get("id")]
    pos_count, rec_count = {}, {}
    if ids:
        ph = ",".join(["%s"] * len(ids))
        try:
            for r in pool.query(f"SELECT company_id, COUNT(*) n FROM positions "
                                f"WHERE company_id IN ({ph}) GROUP BY company_id", tuple(ids)):
                pos_count[r["company_id"]] = int(r["n"])
            for r in pool.query(
                    f"SELECT p.company_id cid, COUNT(*) n FROM contact_records cr "
                    f"JOIN positions p ON p.id = cr.position_id "
                    f"WHERE p.company_id IN ({ph}) GROUP BY p.company_id", tuple(ids)):
                rec_count[r["cid"]] = int(r["n"])
        except Exception:
            pass

    def rank(r):
        src = r.get("intro_source")
        return ((pos_count.get(r["id"], 0) * 5) + (rec_count.get(r["id"], 0) * 3)
                + (3 if src in (0, 2) else 2 if src == 1 else 0)
                + (2 if r.get("introduction") else 0))

    group.sort(key=rank, reverse=True)
    primary = dict(group[0])
    # 主记录缺的字段，从同组其他记录补齐（简介优先取权威来源）
    for r in group[1:]:
        for k in ("industry", "scale"):
            if not primary.get(k) and r.get(k):
                primary[k] = r[k]
        if not primary.get("introduction") and r.get("introduction"):
            primary["introduction"] = r["introduction"]
            primary["intro_source"] = r["intro_source"]
    return group, primary


def collect_facts(name: str, refresh_risk: bool = False) -> dict:
    """把分析要用的事实一次性取齐（公司档案 + 在招职位 + 推荐进展）。"""
    group, co = _company_group(name)
    facts = {
        "company": co,
        "aliases": [r["name"] for r in group if r["name"] != co["name"]],
        "intro": (co.get("introduction") or "").strip(),
        "intro_source": co.get("intro_source"),
        "positions": [], "open_positions": 0, "skills": [],
        "salary_ceiling": None, "salary_text": "",
        "rec_total": 0, "rec_sent": 0, "rec_advanced": 0, "rec_avg_score": None,
        "risk": {}, "risk_summary": {},
    }
    ids = [r["id"] for r in group if r.get("id")]
    if not ids:
        return facts
    ph = ",".join(["%s"] * len(ids))

    try:
        rows = pool.query(
            f"SELECT title, skill_tags, min_years, salary_range, status "
            f"FROM positions WHERE company_id IN ({ph}) ORDER BY status DESC, id DESC",
            tuple(ids))
    except Exception:
        rows = []
    facts["positions"] = rows
    facts["open_positions"] = sum(1 for r in rows if int(r.get("status") or 0) == 1)

    skills, ceiling, sal_texts = [], None, []
    for r in rows:
        try:
            for t in json.loads(r.get("skill_tags") or "[]"):
                t = str(t).strip()
                if t and t not in skills:
                    skills.append(t)
        except Exception:
            pass
        lo, hi = _parse_salary(r.get("salary_range"))
        if hi is not None:
            ceiling = hi if ceiling is None else max(ceiling, hi)
            sal_texts.append(str(r["salary_range"]))
    facts["skills"] = skills[:8]
    facts["salary_ceiling"] = ceiling
    facts["salary_text"] = "、".join(dict.fromkeys(sal_texts))

    try:
        agg = pool.query(
            f"SELECT COUNT(*) n, AVG(cr.match_score) avg, "
            f"SUM(cr.send_status = 'sent') sent, "
            f"SUM(c.status IN ('面试中','已入职')) adv "
            f"FROM contact_records cr "
            f"JOIN positions p ON p.id = cr.position_id "
            f"JOIN candidates c ON c.id = cr.candidate_id "
            f"WHERE p.company_id IN ({ph})", tuple(ids))[0]
        facts["rec_total"] = int(agg["n"] or 0)
        facts["rec_sent"] = int(agg["sent"] or 0)
        facts["rec_advanced"] = int(agg["adv"] or 0)
        facts["rec_avg_score"] = round(float(agg["avg"]), 1) if agg["avg"] is not None else None
    except Exception:
        pass

    # 合规风险核查（企查查三个核查接口，30 天缓存；任一接口未开通都不影响其余维度）
    try:
        facts["risk"] = risk_monitor.get_risk(co.get("id"), co.get("name") or name,
                                              intro=(co.get("introduction") or ""),
                                              refresh=refresh_risk)
    except Exception as exc:
        facts["risk"] = {"available": False, "checked_at": None, "items": [],
                         "error": f"核查异常：{type(exc).__name__}"}
    facts["risk_summary"] = risk_monitor.summarize_payload(facts["risk"])
    return facts


# --------------------------------------------------------------------------- #
# 打分（纯函数，可单测）
# --------------------------------------------------------------------------- #
def score_facts(facts: dict) -> dict:
    """五个维度共 100 分，每一项都带依据，便于解释分数怎么来的。"""
    dims = []

    # 1. 招聘活跃度 25 分
    n = facts.get("open_positions") or 0
    s = 25 if n >= 3 else 19 if n == 2 else 13 if n == 1 else 0
    dims.append({"name": "招聘活跃度", "score": s, "full": 25,
                 "basis": f"在招职位 {n} 个" + (f"（共 {len(facts['positions'])} 个职位）"
                                              if facts.get("positions") else "")})

    # 2. 薪酬吸引力 15 分
    hi = facts.get("salary_ceiling")
    if hi is None:
        s, basis = 6, "未提供薪资区间"
    elif hi >= 45:
        s, basis = 15, f"最高月薪约 {hi:.0f}K（{facts.get('salary_text')}）"
    elif hi >= 30:
        s, basis = 12, f"最高月薪约 {hi:.0f}K（{facts.get('salary_text')}）"
    elif hi >= 20:
        s, basis = 9, f"最高月薪约 {hi:.0f}K（{facts.get('salary_text')}）"
    else:
        s, basis = 5, f"最高月薪约 {hi:.0f}K，偏低（{facts.get('salary_text')}）"
    dims.append({"name": "薪酬吸引力", "score": s, "full": 15, "basis": basis})

    # 3. 推送进展 25 分：发过邀约说明流程真跑起来了，进面试/入职说明企业有反馈
    sent, adv, total = facts.get("rec_sent") or 0, facts.get("rec_advanced") or 0, facts.get("rec_total") or 0
    s = (12 if sent > 0 else 8 if total > 0 else 0) + (13 if adv >= 2 else 8 if adv == 1 else 0)
    s = min(s, 25)
    basis = (f"推荐 {total} 人 · 已发邀约 {sent} 人 · 进入面试/入职 {adv} 人"
             if total else "暂无推荐记录")
    dims.append({"name": "推送进展", "score": s, "full": 25, "basis": basis})

    # 4. 数据可信度 20 分
    src = facts.get("intro_source")
    s = 20 if src in (0, 2) else 9 if src == 1 else 4
    dims.append({"name": "数据可信度", "score": s, "full": 20,
                 "basis": f"企业信息来源：{SOURCE_LABELS.get(src, '未采集')}"})

    # 5. 信息完整度 15 分
    length = len(facts.get("intro") or "")
    s = (4 if (facts["company"].get("industry")) else 0)
    s += 3 if facts["company"].get("scale") else 0
    s += 8 if length >= 100 else 5 if length >= 40 else 3 if length > 0 else 0
    missing = [k for k, ok in (("行业", facts["company"].get("industry")),
                               ("规模", facts["company"].get("scale")),
                               ("简介", length)) if not ok]
    dims.append({"name": "信息完整度", "score": s, "full": 15,
                 "basis": f"简介 {length} 字" + (f"，缺 {'/'.join(missing)}" if missing else "，字段齐全")})

    base_total = sum(d["score"] for d in dims)

    # 6. 合规风险扣分：失信 / 经营异常 / 行政处罚，命中即扣，合计封顶 25 分
    rs = facts.get("risk_summary") or {}
    shixin, exc, pen = (int(rs.get("shixin") or 0), int(rs.get("exception") or 0),
                        int(rs.get("penalty") or 0))
    penalty = min(sum(v for k, v in RISK_PENALTY.items()
                      if {"shixin": shixin, "exception": exc, "penalty": pen}[k] > 0),
                  RISK_PENALTY_CAP)
    total_score = max(0, base_total - penalty)

    # 风险依据（给报告和模型看，明确写清是「查了没有」还是「没查」）
    if not rs.get("checked"):
        risk_basis = "未核查（未配置企查查 Key 或接口未开通）"
    elif not (shixin or exc or pen):
        risk_basis = f"三项核查均未命中（{rs.get('checked_at') or '时间未知'}）"
    else:
        parts = []
        if shixin:
            parts.append(f"失信 {shixin} 条（-{RISK_PENALTY['shixin']}）")
        if exc:
            parts.append(f"经营异常 {exc} 条（-{RISK_PENALTY['exception']}）")
        if pen:
            parts.append(f"行政处罚 {pen} 条（-{RISK_PENALTY['penalty']}）")
        risk_basis = "、".join(parts) + f"，合计扣 {penalty} 分（{rs.get('checked_at') or ''}）"

    level, advice = next((lv, ad) for line, lv, ad in LEVELS if total_score >= line)
    # 硬伤降档：有失信/异常记录时不给「重点合作」，两者同时命中直接「暂缓推荐」
    cap = None
    if shixin:
        cap = "暂缓推荐" if exc else "谨慎评估"
    elif exc:
        cap = "可以推进"
    if cap and _LEVEL_ORDER.index(level) < _LEVEL_ORDER.index(cap):
        level = cap
        advice = next(ad for line, lv, ad in LEVELS if lv == cap)
    return {"total": total_score, "base_total": base_total, "risk_penalty": penalty,
            "risk_basis": risk_basis, "level": level, "advice": advice, "dims": dims}


# --------------------------------------------------------------------------- #
# 结论生成
# --------------------------------------------------------------------------- #
def _risk_brief(facts: dict) -> str:
    """把风险核查结果写成一行事实描述（喂给模型，也用于报告脚注）。"""
    rs = facts.get("risk_summary") or {}
    risk = facts.get("risk") or {}
    if not rs.get("checked"):
        return "未核查（企查查接口未开通或 Key 未配置）"
    hits, misses = [], []
    for item in risk.get("items", []):
        if item.get("state") != "ok":
            misses.append(f"{item['label']}：{item.get('note') or '未完成'}")
        elif item.get("hit"):
            first = (item.get("samples") or [""])[0]
            hits.append(f"{item['label']} {item['count']} 条" + (f"（最近一条：{first}）" if first else ""))
        else:
            misses.append(f"{item['label']}未命中")
    text = "；".join(hits + misses)
    if rs.get("name_hint"):
        text += f"；注意：{rs['name_hint']}"
    return text or "未核查"


def _facts_brief(facts: dict) -> str:
    """给模型看的事实清单（也用于把结论锚在事实上）。"""
    co = facts["company"]
    pos = "；".join(
        f"{p['title']}（{p.get('min_years') or 0} 年以上，{p.get('salary_range') or '薪资未标'}，"
        f"{'在招' if int(p.get('status') or 0) == 1 else '已关闭'}）"
        for p in facts["positions"][:6]) or "无"
    return (
        f"公司：{co['name']}\n"
        f"行业：{co.get('industry') or '未填写'}｜规模：{co.get('scale') or '未填写'}\n"
        f"企业信息来源：{SOURCE_LABELS.get(facts.get('intro_source'), '未采集')}\n"
        f"简介：{(facts.get('intro') or '（空）')[:400]}\n"
        f"在招职位：{facts['open_positions']} 个｜职位明细：{pos}\n"
        f"职位要求的核心技能：{'、'.join(facts['skills']) or '未标注'}\n"
        f"薪资区间：{facts.get('salary_text') or '未提供'}\n"
        f"本库进展：推荐 {facts['rec_total']} 人，已发邀约 {facts['rec_sent']} 人，"
        f"进入面试/入职 {facts['rec_advanced']} 人，平均匹配分 "
        f"{facts['rec_avg_score'] if facts['rec_avg_score'] is not None else '无'}\n"
        f"合规风险（企查查核查）：{_risk_brief(facts)}"
    )


_PROMPT = """你是资深猎头顾问，正在评估「这家公司值不值得把候选人推荐过去」。
只能使用下面【事实】里的信息，不要编造融资轮次、营收、员工人数、司法诉讼等没有提供的内容；
无法确认的点，写进 risks 时以「待核实：」开头。

【事实】
{brief}

【评分】{score} 分 / 100（基础分 {base}，合规风险扣 {penalty} 分），结论档位：{level}

合规风险的写法要求：
- 事实里给出了失信/经营异常/行政处罚的条数时，直接在 risks 里引用条数与最近一条概要，不要再写「待核实」；
- 事实里写「未核查」时，可以写「待核实：未做工商风险核查」，但不要断言该公司没有风险；
- 不要自行补充事实里没有的诉讼、处罚细节。

只输出 JSON，不要 markdown 代码块，格式：
{{"summary": "一句话定性，30 字内",
  "strengths": ["3-4 条优势，每条 15-40 字，必须能对应上面的事实"],
  "risks": ["3-4 条风险或待核实项，每条 15-40 字"],
  "fit_profiles": ["2-3 条最适合优先推送的候选人画像，具体到技能/年限/诉求"],
  "questions": ["3-4 条建议先向企业确认的问题，猎头尽调常用角度"]}}"""


def _coerce_list(v, limit=5) -> list[str]:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for x in v:
        if isinstance(x, (str, int, float)):
            t = str(x).strip().lstrip("-·• ").strip()
            if t:
                out.append(t)
        if len(out) >= limit:
            break
    return out


def _parse_json(text: str | None) -> dict | None:
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        data = json.loads(t[i:j + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _rule_insights(facts: dict, score: dict) -> dict:
    """模型不可用时的规则版结论：全部由事实推出，不掺猜测。"""
    co = facts["company"]
    strengths, risks, profiles, questions = [], [], [], []

    if facts["open_positions"]:
        titles = "、".join(p["title"] for p in facts["positions"] if int(p.get("status") or 0) == 1)
        strengths.append(f"在招 {facts['open_positions']} 个岗位（{titles[:40]}），有真实招聘需求")
    if facts.get("salary_ceiling") and facts["salary_ceiling"] >= 30:
        strengths.append(f"薪资上限约 {facts['salary_ceiling']:.0f}K/月，对候选人有一定吸引力")
    if facts["rec_sent"]:
        strengths.append(f"已成功触达 {facts['rec_sent']} 位候选人，沟通链路走得通")
    if facts["intro_source"] in (0, 2):
        strengths.append(f"企业信息来自{SOURCE_LABELS[facts['intro_source']]}，背景可公开核对")
    if len(facts.get("intro") or "") >= 100:
        strengths.append("企业介绍信息较完整，主营业务清晰")

    if facts["intro_source"] == 1:
        risks.append("待核实：企业简介为 AI 生成，未经权威数据源确认")
    if not facts["rec_total"]:
        risks.append("待核实：尚无该公司的推荐记录，缺少真实推进反馈")
    if not facts["open_positions"]:
        risks.append("待核实：库内没有在招职位，岗位真实性无法判断")
    if not co.get("scale"):
        risks.append("待核实：企业规模未登记，团队稳定性不好判断")
    if facts.get("salary_ceiling") is None:
        risks.append("待核实：未提供薪资区间，候选人最关心的条件未知")
    if facts["positions"] and all(not p.get("salary_range") for p in facts["positions"]):
        risks.append("待核实：职位均未标注薪资，可能存在预算不明确")
    if facts.get("rec_avg_score") is not None and facts["rec_avg_score"] < 70:
        risks.append(f"已推荐候选人平均匹配分仅 {facts['rec_avg_score']}，说明需求与人才市场不太对齐")

    # 合规风险（企查查核查结果）：有记录就摆在最前面，这是猎头最该知道的硬信息
    rs = facts.get("risk_summary") or {}
    if rs.get("checked"):
        for item in (facts.get("risk") or {}).get("items", []):
            if item.get("hit"):
                first = (item.get("samples") or [""])[0]
                risks.insert(0, f"{item['label']}命中 {item['count']} 条"
                                + (f"：{first}" if first else ""))
        if not rs.get("shixin") and not rs.get("exception") and not rs.get("penalty"):
            strengths.append(f"工商合规核查未发现失信、经营异常或行政处罚记录"
                             f"（企查查，{rs.get('checked_at') or '近期'}）")
    else:
        risks.append("待核实：尚未做工商风险核查（企查查接口未开通或未配置 Key）")
    if rs.get("name_hint"):
        risks.append(f"待核实：{rs['name_hint']}")

    yrs = [int(p.get("min_years") or 0) for p in facts["positions"]] or [0]
    need = max(yrs)
    skills = "、".join(facts["skills"][:3]) or "岗位未标注技能"
    profiles.append(f"{need} 年以上经验、具备 {skills} 的技术候选人")
    if facts["rec_sent"] == 0:
        profiles.append("愿意先聊一聊、对薪资要求不极端的被动求职者")
    else:
        profiles.append("已在其他公司面试中、可快速到岗的候选人")

    questions.append("这个岗位是新增编制还是替补？前任离职原因是什么？")
    questions.append(f"薪资区间 {facts.get('salary_text') or '尚未明确'} 是否含绩效与年终，几薪？")
    questions.append("团队规模、汇报线和跨部门协作方式是怎样的？")
    questions.append("面试流程几轮、谁拍板、平均决策周期多久？")

    return {
        "summary": f"{co['name']}：{score['level']}，{score['advice']}",
        "strengths": strengths[:4] or ["暂无可核验的正面支撑点"],
        "risks": risks[:4] or ["暂无显性风险，仍需按常规尽调流程确认"],
        "fit_profiles": profiles[:3],
        "questions": questions[:4],
        "engine": "rule",
    }


def _llm_insights(facts: dict, score: dict) -> dict | None:
    prompt = _PROMPT.format(brief=_facts_brief(facts), score=score["total"], level=score["level"],
                            base=score.get("base_total", score["total"]),
                            penalty=score.get("risk_penalty", 0))
    data = _parse_json(llm.chat(prompt, system="你是严谨的猎头顾问，只基于给定事实输出 JSON。"))
    if not data:
        return None
    strengths = _coerce_list(data.get("strengths"))
    risks = _coerce_list(data.get("risks"))
    if not strengths or not risks:
        return None
    profiles = _coerce_list(data.get("fit_profiles"), 3)
    if not profiles and facts["positions"]:
        profiles = [f"{facts['positions'][0].get('min_years') or 0} 年以上、技能匹配的候选人"]
    return {
        "summary": str(data.get("summary") or "").strip()[:120] or
                   f"{facts['company']['name']}：{score['level']}",
        "strengths": strengths,
        "risks": risks,
        "fit_profiles": profiles,
        "questions": _coerce_list(data.get("questions"), 4),
        "engine": "llm",
    }


# --------------------------------------------------------------------------- #
# 结果持久化
# --------------------------------------------------------------------------- #
def _save(company_id: int | None, result: dict):
    if not company_id:
        return
    try:
        pool.execute(
            "INSERT INTO company_analyses (company_id, score, level, summary, strengths, risks, "
            "fit_profiles, questions, verdict, facts_snapshot, model) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (company_id, result["score"]["total"], result["score"]["level"],
             result["insights"]["summary"],
             json.dumps(result["insights"]["strengths"], ensure_ascii=False),
             json.dumps(result["insights"]["risks"], ensure_ascii=False),
             json.dumps(result["insights"]["fit_profiles"], ensure_ascii=False),
             json.dumps(result["insights"]["questions"], ensure_ascii=False),
             result["score"]["advice"],
             json.dumps(_snapshot(result["facts"]), ensure_ascii=False),
             result["model"]))
    except Exception:
        pass  # 留痕失败不影响分析结果返回


def _snapshot(facts: dict) -> dict:
    return {
        "name": facts["company"].get("name"),
        "aliases": facts.get("aliases", []),
        "open_positions": facts["open_positions"],
        "positions": [p.get("title") for p in facts["positions"]],
        "salary": facts.get("salary_text"),
        "rec_total": facts["rec_total"], "rec_sent": facts["rec_sent"],
        "rec_advanced": facts["rec_advanced"], "rec_avg_score": facts["rec_avg_score"],
        "intro_source": facts.get("intro_source"), "intro_len": len(facts.get("intro") or ""),
        "risk": {k: int((facts.get("risk_summary") or {}).get(k) or 0)
                 for k in ("shixin", "exception", "penalty")},
        "risk_checked": bool((facts.get("risk_summary") or {}).get("checked")),
    }


def latest_analysis(name: str) -> dict | None:
    rows = pool.query(
        "SELECT a.id, a.score, a.level, a.summary, a.created_at, a.model "
        "FROM company_analyses a JOIN companies c ON c.id = a.company_id "
        "WHERE c.name = %s ORDER BY a.id DESC LIMIT 1", (name,))
    if not rows:
        return None
    r = dict(rows[0])
    at = r.get("created_at")
    r["t"] = at.strftime("%Y-%m-%d %H:%M") if hasattr(at, "strftime") else str(at or "")
    return r


def _load_cached(company_id: int | None, snap_json: str) -> dict | None:
    """数据没变化就复用上次结论，避免同一家公司反复烧模型额度。"""
    if not company_id:
        return None
    rows = pool.query(
        "SELECT * FROM company_analyses WHERE company_id = %s ORDER BY id DESC LIMIT 1",
        (company_id,))
    if not rows:
        return None
    r = rows[0]
    try:
        stored = json.loads(r["facts_snapshot"] or "{}")
    except Exception:
        return None
    if json.dumps(stored, ensure_ascii=False, sort_keys=True) != snap_json:
        return None

    def items(col):
        try:
            return json.loads(r[col] or "[]")
        except Exception:
            return []

    model = str(r.get("model") or "")
    return {
        "summary": r.get("summary") or "",
        "strengths": items("strengths"),
        "risks": items("risks"),
        "fit_profiles": items("fit_profiles"),
        "questions": items("questions"),
        "engine": "rule" if model.startswith("规则") else "llm",
        "model": model,
        "cached_at": str(r.get("created_at") or ""),
    }


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def analyze_company(name: str, refresh: bool = False, refresh_risk: bool = False) -> dict:
    """返回 {company, facts, score, insights, model, at, cached}。"""
    facts = collect_facts(name, refresh_risk=refresh_risk)
    score = score_facts(facts)
    snap_json = json.dumps(_snapshot(facts), ensure_ascii=False, sort_keys=True)

    cached = None if refresh else _load_cached(facts["company"].get("id"), snap_json)
    if cached:
        insights = cached
    else:
        insights = _llm_insights(facts, score) or _rule_insights(facts, score)
        result = {"company": facts["company"], "facts": facts, "score": score,
                  "insights": insights, "model": llm.model if insights["engine"] == "llm"
                  else "规则引擎（未调用大模型）"}
        _save(facts["company"].get("id"), result)

    row = latest_analysis(facts["company"].get("name") or name)
    return {
        "company": facts["company"],
        "facts": facts,
        "score": score,
        "insights": insights,
        "model": insights.get("model") or (llm.model if insights["engine"] == "llm"
                                          else "规则引擎（未调用大模型）"),
        "at": row["t"] if row else None,
        "cached": bool(cached),
    }


def _bar(score: int, full: int, width: int = 10) -> str:
    filled = round(score / full * width) if full else 0
    return "█" * filled + "░" * (width - filled)


def render_markdown(result: dict) -> str:
    """渲染成聊天窗口直接可读的分析报告。"""
    co, facts, score, ins = (result["company"], result["facts"],
                             result["score"], result["insights"])
    lines = [f"### {co['name']} · 公司分析", ""]

    source = SOURCE_LABELS.get(facts.get("intro_source"), "未采集")
    head = [f"行业：{co.get('industry') or '未填写'}", f"规模：{co.get('scale') or '未填写'}",
            f"在招岗位：{facts['open_positions']} 个",
            f"信息来源：{source}"]
    if facts.get("aliases"):
        head.append(f"同名词条已归并：{'、'.join(facts['aliases'])}")
    base_note = (f"（基础分 {score['base_total']}，合规风险扣 {score['risk_penalty']} 分）"
                 if score.get("risk_penalty") else "")
    lines.append(f"**综合评分 {score['total']} / 100　·　{score['level']}**{base_note}")
    lines.append("")
    lines.append("　|　".join(head))
    lines.append("")
    lines.append("| 维度 | 得分 | 依据 |")
    lines.append("|---|---|---|")
    for d in score["dims"]:
        lines.append(f"| {d['name']} | {d['score']}/{d['full']} {_bar(d['score'], d['full'])} | {d['basis']} |")
    penalty = score.get("risk_penalty") or 0
    lines.append(f"| 合规风险 | {'-' + str(penalty) if penalty else '0'} | {score.get('risk_basis', '')} |")
    lines.append("")

    if ins.get("summary"):
        lines.append(f"> {ins['summary']}")
        lines.append("")

    lines.append("**优势**")
    lines += [f"- {x}" for x in ins["strengths"]] or ["- 暂无"]
    lines.append("")
    risk = facts.get("risk") or {}
    if risk.get("items"):
        lines.append("**合规风险核查**（企查查开放平台）")
        for item in risk.get("items", []):
            if item.get("state") != "ok":
                lines.append(f"- {item['label']}：{item.get('note') or '未完成'}")
            elif item.get("hit"):
                lines.append(f"- {item['label']}：**命中 {item['count']} 条**")
                for sample in item.get("samples", []):
                    lines.append(f"    - {sample}")
            else:
                lines.append(f"- {item['label']}：未命中")
        hint = (facts.get("risk_summary") or {}).get("name_hint")
        if hint:
            lines.append(f"- 注意：{hint}")
        lines.append(f"<span style='color:#94a3b8;font-size:12px'>核查时间 "
                     f"{risk.get('checked_at') or '-'}，同一企业 30 天内复用结果</span>")
        lines.append("")

    lines.append("**风险 / 待核实**")
    lines += [f"- {x}" for x in ins["risks"]] or ["- 暂无"]
    lines.append("")

    if ins["fit_profiles"]:
        lines.append("**适合优先推送的候选人**")
        lines += [f"- {x}" for x in ins["fit_profiles"]]
        lines.append("")
    if ins["questions"]:
        lines.append("**建议先向企业确认**")
        lines += [f"- {x}" for x in ins["questions"]]
        lines.append("")

    lines.append(f"**综合建议**　{score['advice']}")
    lines.append("")
    cache_note = (f"本次复用 {result['at']} 的结论（库内数据未变化，点「重新分析」可重算）。"
                  if result.get("cached") else "")
    lines.append(
        f"<span style='color:#64748b;font-size:12px'>{cache_note}"
        f"评分由可核验数据计算（职位 / 薪资区间 / 推荐进展 / 信息来源 / 信息完整度），主观结论由"
        f"{'大模型' if ins['engine'] == 'llm' else '规则引擎'}生成，仅供决策参考；"
        f"融资、营收等信息未纳入评分，建议在企业征信平台进一步核实。</span>")
    return "\n".join(lines)
