"""匹配引擎单元测试：覆盖边界用例。运行: pytest tests/ -v"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.matcher import exp_score, match, normalize_skill, skill_score


class TestNormalizeSkill:
    def test_synonym(self):
        assert normalize_skill("JS") == "javascript"
        assert normalize_skill("LLM") == "大模型"

    def test_case_insensitive(self):
        assert normalize_skill(" Python ") == "python"


class TestSkillScore:
    def test_full_hit(self):
        assert skill_score(["Python", "SQL"], ["python", "sql"]) == 100.0

    def test_partial_hit(self):
        assert skill_score(["Python"], ["Python", "SQL", "Redis"]) == round(100 / 3, 2)

    def test_no_hit(self):
        assert skill_score(["Java"], ["Python"]) == 0.0

    def test_empty_position_tags_neutral(self):
        assert skill_score(["Python"], []) == 60.0


class TestExpScore:
    def test_meets(self):
        assert exp_score(5, 5) == 100.0

    def test_over(self):
        assert exp_score(10, 3) == 100.0  # 超出不加分

    def test_one_year_short(self):
        assert exp_score(4, 5) == 75.0

    def test_way_short(self):
        assert exp_score(0, 5) == 0.0  # 下限 0


class TestMatch:
    POS = {"title": "AI工程师", "skill_tags": ["Python", "LangChain", "RAG"], "min_years": 4}

    def test_full_hit_recommend(self):
        cand = {"name": "张三", "skill_tags": ["Python", "LangChain", "RAG"], "years_of_exp": 5}
        r = match(cand, self.POS)
        assert r["skill_score"] == 100.0
        assert r["exp_score"] == 100.0
        assert r["recommend"] is True
        assert r["total"] == 100.0

    def test_two_thirds_hit_borderline(self):
        cand = {"name": "张三", "skill_tags": ["Python", "LangChain", "SQL"], "years_of_exp": 5}
        r = match(cand, self.POS)
        assert r["skill_score"] == round(2 / 3 * 100, 2)
        assert r["total"] < 80
        assert r["recommend"] is False

    def test_not_recommend(self):
        weak = {"name": "李四", "skill_tags": ["Java"], "years_of_exp": 1}
        r = match(weak, self.POS)
        assert r["recommend"] is False
        assert r["total"] < 80


class TestHardExpGate:
    """硬性经验门槛：技能满分但经验不足 → 不通过，权重再高也拦下。"""

    POS = {"title": "资深算法工程师", "skill_tags": ["Python", "LangChain", "RAG"], "min_years": 3}

    def test_skill_full_but_zero_exp_blocked(self):
        cand = {"name": "应届牛人", "skill_tags": ["Python", "LangChain", "RAG"], "years_of_exp": 0}
        r = match(cand, self.POS)
        assert r["skill_score"] == 100.0
        assert r["hard_fail"] is True
        assert r["passed"] is False
        assert r["recommend"] is False
        assert r["grade"] == "不通过"

    def test_hard_gate_survives_high_skill_weight(self):
        cand = {"name": "应届牛人", "skill_tags": ["Python", "LangChain", "RAG"], "years_of_exp": 0}
        r = match(cand, self.POS, weights={"skill": 0.99, "exp": 0.01, "threshold": 50})
        assert r["total"] == 99.25  # 技能权重拉到 99% 也不放行
        assert r["recommend"] is False

    def test_meets_min_years_passes_gate(self):
        cand = {"name": "达标者", "skill_tags": ["Python"], "years_of_exp": 3}
        r = match(cand, self.POS, weights={"skill": 0.7, "exp": 0.3, "threshold": 80})
        assert r["hard_fail"] is False
        assert r["passed"] is True

    def test_no_min_years_no_gate(self):
        pos = {"title": "实习生", "skill_tags": ["Python"], "min_years": 0}
        cand = {"name": "新人", "skill_tags": ["Python"], "years_of_exp": 0}
        r = match(cand, pos)
        assert r["hard_fail"] is False
        assert r["recommend"] is True
