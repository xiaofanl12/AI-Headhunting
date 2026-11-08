"""LangGraph 组装：意图路由 -> 各业务节点 -> END。"""
from langgraph.graph import END, StateGraph

from graph.nodes import analysis_node, email_node, executor, matcher_node
from graph.nodes.intent_router import classify
from graph.state import GraphState


def route_by_intent(state: GraphState) -> str:
    return state["intent"]


def chitchat_node(state: GraphState) -> GraphState:
    from core.llm import llm
    text = llm.chat(state["user_input"],
                    system="你是AI猎头助手，可帮助HR查询候选人、匹配职位、背调公司与发送邀约邮件。")
    return {**state, "reply": text or "我是AI猎头助手，试试：'查一下有5年经验的Python候选人'。"}


def _respond_query(state: GraphState) -> GraphState:
    """格式化查询结果；若前面节点已给出回复（如安全拦截），保留原回复不覆盖。"""
    if state.get("reply") or not state.get("sql"):
        return state
    rows = (state.get("query_result") or [{}])[0].get("rows")
    if rows is None:  # 写操作：返回受影响行数
        affected = (state.get("query_result") or [{}])[0].get("affected", 0)
        return {**state, "reply": f"操作已执行，影响 {affected} 行。"}
    return {**state, "reply": executor.format_rows(rows)}


def build_graph() -> StateGraph:
    g = StateGraph(GraphState)
    g.set_entry_point("intent_router")
    g.add_node("intent_router", lambda s: {**s, "intent": classify(s["user_input"])})
    g.add_node("nl2sql", executor.nl2sql_node)
    g.add_node("executor", executor.executor_node)
    g.add_node("responder_query", _respond_query)
    g.add_node("matcher", matcher_node.matcher_node)
    g.add_node("company_lookup", email_node.company_node)
    g.add_node("company_analysis", analysis_node.analysis_node)
    g.add_node("email_sender", email_node.email_node)
    g.add_node("chitchat", chitchat_node)

    g.add_conditional_edges("intent_router", route_by_intent, {
        "query": "nl2sql",
        "write": "nl2sql",
        "match": "matcher",
        "company_lookup": "company_lookup",
        "company_analysis": "company_analysis",
        "send_email": "email_sender",
        "chitchat": "chitchat",
    })
    # query 链路: nl2sql -> executor -> responder_query -> END
    g.add_edge("nl2sql", "executor")
    g.add_edge("executor", "responder_query")
    for node in ("responder_query", "matcher", "company_lookup", "company_analysis",
                 "email_sender", "chitchat"):
        g.add_edge(node, END)
    return g


app_graph = build_graph().compile()
