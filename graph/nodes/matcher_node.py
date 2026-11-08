"""匹配节点：从用户指令中解析职位与阈值，调用确定性匹配引擎。"""
import json

from core import matcher
from core.llm import llm
from db import pool
from graph.state import GraphState

DEFAULT_MIN_YEARS = 3


def matcher_node(state: GraphState) -> GraphState:
    # 取所有招聘中的职位：优先匹配用户指令中提到的职位名，否则取最新一个
    positions = pool.query(
        "SELECT p.*, c.name AS company_name FROM positions p "
        "JOIN companies c ON c.id = p.company_id WHERE p.status = 1 ORDER BY p.id DESC"
    )
    if not positions:
        return {**state, "reply": "当前没有招聘中的职位，请先添加职位。"}
    position = next(
        (p for p in positions if p["title"] and p["title"] in state["user_input"]),
        positions[0],
    )
    position = dict(position)
    position["skill_tags"] = json.loads(position["skill_tags"] or "[]")

    candidates = pool.query("SELECT * FROM candidates WHERE status IN ('待筛选','待推荐')")
    results = []
    all_scores: list[tuple[str, float]] = []
    hard_blocked: list[str] = []
    for cand in candidates:
        cand = dict(cand)
        cand["skill_tags"] = json.loads(cand["skill_tags"] or "[]")
        result = matcher.match(cand, position)
        if result["hard_fail"]:
            hard_blocked.append(cand["name"])
            continue
        all_scores.append((cand["name"], result["total"]))
        if result["recommend"]:
            reason = matcher.build_reason(cand, position, result, llm.chat(
                f"用两句话说明候选人为什么适合职位。候选人技能:{cand['skill_tags']}，"
                f"经验{cand['years_of_exp']}年；职位:{position['title']}，"
                f"要求技能:{position['skill_tags']}，最低{position['min_years']}年。命中:{result['hit_skills']}"
            ))
            # uk_cand_pos 唯一约束保证同一(候选人,职位)只有一条记录，重复匹配时更新评分而非报错
            pool.execute(
                "INSERT INTO contact_records (candidate_id, position_id, match_score, skill_score, exp_score, reason) "
                "VALUES (%s,%s,%s,%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE match_score = VALUES(match_score), skill_score = VALUES(skill_score), "
                "exp_score = VALUES(exp_score), reason = VALUES(reason), "
                "hr_decision = IF(match_score <> VALUES(match_score), NULL, hr_decision)",
                (cand["id"], position["id"], result["total"], result["skill_score"], result["exp_score"], reason),
            )
            pool.execute("UPDATE candidates SET status = '已推荐' WHERE id = %s", (cand["id"],))
            results.append({"candidate": cand["name"], "score": result["total"],
                            "grade": result.get("grade", "推荐"), "reason": reason})

    if not results:
        # 无达标者时给出最接近的候选人，便于 HR 判断差在哪
        top = max(all_scores, key=lambda x: x[1], default=None)
        already = pool.query(
            "SELECT COUNT(*) n FROM contact_records WHERE position_id = %s", (position["id"],))[0]["n"]
        msg = f"职位「{position['title']}」暂无总分 ≥ {matcher.get_weights()['threshold']} 的候选人。"
        if top:
            msg += f"\n最接近的是「{top[0]}」，{top[1]} 分，距阈值差 {round(matcher.get_weights()['threshold'] - top[1], 2)} 分。"
        if already:
            msg += f"\n该职位已有 {already} 条推荐记录（对应候选人状态已变为「已推荐」，不再重复进入候选池）。"
        if hard_blocked:
            msg += (f"\n另有 {len(hard_blocked)} 人未通过硬性经验门槛（要求 ≥ {position.get('min_years', 0)} 年）："
                    f"{'、'.join(hard_blocked[:5])}{'…' if len(hard_blocked) > 5 else ''}，无论技能分多高均不推荐。")
        return {**state, "reply": msg}
    results.sort(key=lambda r: r["score"], reverse=True)
    lines = "\n".join(f"{i}. {r['candidate']}（{r['score']} 分 · {r['grade']}）：{r['reason']}"
                      for i, r in enumerate(results, 1))
    suffix = (f"\n另有 {len(hard_blocked)} 人未通过硬性经验门槛（要求 ≥ {position.get('min_years', 0)} 年），"
              f"技能分再高也不进入推荐。" if hard_blocked else "")
    return {**state, "match_result": results,
            "reply": f"职位「{position['title']}」推荐结果（≥{matcher.get_weights()['threshold']} 分，档位见括号，"
                     f"邀约需 HR 查阅简历后人工确认）：\n{lines}{suffix}"}
