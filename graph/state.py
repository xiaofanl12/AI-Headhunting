"""LangGraph 全局状态定义。"""
from typing import TypedDict


class GraphState(TypedDict, total=False):
    session_id: str
    user_input: str
    intent: str            # query/match/company_lookup/company_analysis/send_email/chitchat
    sql: str
    query_result: list     # 查询结果行
    match_result: list     # 匹配评分明细
    company_info: dict     # 公司背调结果
    company_analysis: dict # 公司价值分析（评分 + 优劣点 + 建议）
    email_draft: dict      # {candidate, position, company, subject, body}
    reply: str             # 回给用户的自然语言
