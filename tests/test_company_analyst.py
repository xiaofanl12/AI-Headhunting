"""公司分析引擎单测：打分口径、薪资解析、名称归并、报告渲染。

这些用例不连数据库，保证评分逻辑可离线验证、口径改动时能被测试拦住。
"""
import pytest

from core.company_analyst import (_core_name, _parse_json, _parse_salary, render_markdown,
                                  score_facts)


def _facts(**kw):
    base = {
        "company": {"id": 1, "name": "测试科技", "industry": "人工智能", "scale": "500-2000人",
                    "introduction": "一家做人工智能的公司。" * 12},
        "aliases": [],
        "intro": "一家做人工智能的公司。" * 12,
        "intro_source": 0,
        "positions": [{"title": "AI工程师", "skill_tags": '["Python","RAG"]', "min_years": 4,
                       "salary_range": "40-60K", "status": 1}],
        "open_positions": 1,
        "skills": ["Python", "RAG"],
        "salary_ceiling": 60.0,
        "salary_text": "40-60K",
        "rec_total": 3, "rec_sent": 2, "rec_advanced": 1, "rec_avg_score": 88.0,
    }
    base.update(kw)
    return base


class TestSalary:
    @pytest.mark.parametrize("text,expected", [
        ("40-60K", (40.0, 60.0)),
        ("30-60万/年", (25.0, 50.0)),
        ("25K", (25.0, 25.0)),
        (None, (None, None)),
        ("面议", (None, None)),
    ])
    def test_parse(self, text, expected):
        assert _parse_salary(text) == expected


class TestCoreName:
    @pytest.mark.parametrize("raw,core", [
        ("智谱AI", "智谱"),
        ("智谱", "智谱"),
        ("示例科技有限公司", "示例"),
        (" 北京智谱华章科技有限公司 ", "北京智谱华章"),
    ])
    def test_core(self, raw, core):
        assert _core_name(raw) == core


class TestScore:
    def test_rich_company_scores_high(self):
        r = score_facts(_facts())
        # 13(1 个在招) + 15(薪资≥45K) + 20(发过邀约 + 1 人进面) + 20(百科) + 15(信息全)
        assert r["total"] == 83
        assert r["level"] == "重点合作"

    def test_empty_company_scores_low(self):
        r = score_facts(_facts(positions=[], open_positions=0, skills=[], intro="",
                               intro_source=None, salary_ceiling=None, salary_text="",
                               rec_total=0, rec_sent=0, rec_advanced=0, rec_avg_score=None,
                               company={"id": 2, "name": "空壳", "industry": None,
                                        "scale": None, "introduction": None}))
        assert r["total"] == 10  # 薪酬 6 + 可信度 4
        assert r["level"] == "暂缓推荐"

    def test_ai_intro_lowers_trust(self):
        good = score_facts(_facts())
        bad = score_facts(_facts(intro_source=1))
        assert good["total"] - bad["total"] == 11

    def test_dims_have_basis(self):
        for d in score_facts(_facts())["dims"]:
            assert d["basis"] and len(d["basis"]) > 3
            assert 0 <= d["score"] <= d["full"]


class TestJsonParse:
    def test_fenced_json(self):
        assert _parse_json('```json\n{"a": [1]}\n```') == {"a": [1]}

    def test_garbage(self):
        assert _parse_json("模型没按格式输出") is None
        assert _parse_json(None) is None


def test_render_contains_sections():
    result = {
        "company": {"name": "测试科技"},
        "facts": _facts(),
        "score": score_facts(_facts()),
        "insights": {"summary": "一句话", "strengths": ["优势一"], "risks": ["待核实：风险一"],
                     "fit_profiles": ["4 年以上 Python 工程师"], "questions": ["编制是新增还是替补？"],
                     "engine": "rule"},
        "model": "规则引擎（未调用大模型）", "at": "2026-11-04 14:00", "cached": True,
    }
    md = render_markdown(result)
    for token in ("综合评分", "优势", "风险 / 待核实", "适合优先推送的候选人",
                  "建议先向企业确认", "综合建议", "复用"):
        assert token in md
