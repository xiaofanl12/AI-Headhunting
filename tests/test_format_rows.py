"""format_rows 中文格式化输出测试。"""
from graph.nodes.executor import format_rows


def test_empty():
    assert format_rows([]) == "查询结果为空。"


def test_candidate_full_info():
    rows = [{
        "id": 201, "name": "张三", "phone": "13812345678",
        "email": "zhangsan@example.com", "resume_text": "后端工程师，擅长推荐系统",
        "skill_tags": '["Python","MySQL"]', "years_of_exp": 6.0,
        "education": "本科", "status": "待筛选", "data_source": "manual",
    }]
    out = format_rows(rows)
    assert "**1. 张三**" in out and "`编号 201`" in out and "`待筛选`" in out
    assert "电话**：138****5678" in out           # 手机号脱敏
    assert "邮箱**：zhangsan@example.com" in out
    assert "技能标签**：Python、MySQL" in out      # JSON -> 顿号分隔
    assert "工作年限**：6 年" in out
    assert "学历**：本科" in out
    assert "数据来源**：人工录入" in out
    assert "[查看完整简历](/resume/201)" in out    # 简历链接
    assert "id=" not in out and "years_of_exp" not in out  # 不暴露英文字段名


def test_missing_fields_shown():
    rows = [{"id": 1, "name": "李四", "phone": None, "email": None,
             "resume_text": "有简历"}]
    out = format_rows(rows)
    assert "电话**：未填写" in out and "邮箱**：未填写" in out
    assert "[查看完整简历](/resume/1)" in out


def test_no_resume_no_link():
    rows = [{"id": 2, "name": "王五", "resume_text": None}]
    out = format_rows(rows)
    assert "查看完整简历" not in out


def test_count_aggregate():
    assert format_rows([{"n": 125}]) == "共查询到 125 条符合条件的记录。"


def test_non_candidate_table():
    rows = [{"id": 62, "name": "智谱", "industry": "人工智能", "scale": None}]
    out = format_rows(rows)
    assert "**1. 智谱**" in out and "`编号 62`" in out
    assert "所属行业**：人工智能" in out
    assert "公司规模**：未填写" in out


def test_position_status_kept():
    """positions 等无 name 的行走表格：枚举值仍要翻译，字段名仍要中文。"""
    rows = [{"id": 1, "title": "AI工程师", "status": 1, "min_years": 3}]
    out = format_rows(rows)
    assert "共 1 条结果" in out
    assert "招聘中" in out and "3 年" in out
    assert "职位名称" in out and "经验要求" in out
    assert "status" not in out and "min_years" not in out


def test_aggregate_multi_row_table():
    """多行聚合（GROUP BY）用表格，不能出现「**1.** 状态：已推荐 | cnt：1」这种空标题卡片。"""
    rows = [{"status": "待筛选", "cnt": 124}, {"status": "已推荐", "cnt": 1}]
    out = format_rows(rows)
    assert "共 2 条结果" in out
    assert "| 状态 | 数量 |" in out
    assert "124" in out and "**1.**" not in out
