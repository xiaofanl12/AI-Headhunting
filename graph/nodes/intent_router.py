"""意图识别：关键词快路径优先（零成本、可离线），兜底 GLM 分类。

query=只读查询（仅允许 SELECT）；write=增删改（允许 INSERT/UPDATE/DELETE）。
两者分开是为了让 SQL 校验器能按意图收窄权限。
"""
import re

from core.llm import llm

INTENTS = ("query", "write", "match", "company_lookup", "company_analysis",
           "send_email", "chitchat")

# 「分析这家公司靠不靠谱 / 值不值得推人」——注意区别于查询候选人
_ANALYSIS_RE = re.compile(r"分析|评估|尽调|优劣势|优势|短板|风险|值不值得|值得推|靠不靠谱|靠谱吗|适不适合推")
_NOT_COMPANY_RE = re.compile(r"候选人|简历|人才|职位|岗位")

_RULES: list[tuple[str, re.Pattern]] = [
    ("send_email", re.compile(r"发(送)?(邮件|邀约|邀请)|面试邀约|发offer", re.I)),
    ("company_analysis", re.compile(r"(分析|评估|尽调|看(一下|看)).{0,12}(公司|企业)|"
                                    r"(公司|企业).{0,8}(分析|评估|优劣|风险)|"
                                    r"值不值得推|适不适合推|靠不靠谱|靠谱吗")),
    ("match", re.compile(r"匹配|推荐候选人|筛选|打分")),
    ("company_lookup", re.compile(r"(这家|那家|了解|查一下|介绍).{0,8}公司|公司(怎么样|背景|介绍)")),
    ("write", re.compile(r"新增|插入|添加|录入|更新|修改|删除|移除|设置为|改成")),
    ("query", re.compile(r"查(询|一下|找)|列出|有多少|统计|显示|看看")),
]


def classify(user_input: str) -> str:
    text = user_input.strip()
    # 「分析 / 优劣势 / 值不值得推」类表达，只要不是在说候选人或职位，就归到公司分析
    if _ANALYSIS_RE.search(text) and not _NOT_COMPANY_RE.search(text):
        return "company_analysis"
    for intent, pattern in _RULES:
        if pattern.search(text):
            return intent
    # LLM 兜底分类
    raw = llm.chat(
        f"将下面这条HR指令分类为 query/write/match/company_lookup/company_analysis/send_email/chitchat 之一"
        f"（query 只读查询，write 增删改，company_analysis 评估公司是否值得推荐候选人），只输出类别单词。\n指令：{text}"
    )
    if raw:
        intent = raw.strip().lower().strip("。.")
        if intent in INTENTS:
            return intent
    return "chitchat"
