"""公司价值分析节点：评估这家公司值不值得把候选人推过去。"""
from core.company_analyst import analyze_company, render_markdown
from graph.nodes.email_node import _extract_company_name
from graph.state import GraphState


def analysis_node(state: GraphState) -> GraphState:
    name = _extract_company_name(state["user_input"])
    result = analyze_company(name)
    return {**state, "company_info": result["company"],
            "company_analysis": result, "reply": render_markdown(result)}
