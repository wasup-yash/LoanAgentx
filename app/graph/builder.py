from langgraph.graph import StateGraph, END, START

from app.graph.nodes.chase import make_chase_node
from app.graph.nodes.extract_trace import make_extract_trace_node
from app.graph.nodes.ingest_route import make_ingest_route_node
from app.graph.nodes.reconcile import make_reconcile_node
from app.graph.state import LoanAgentState
from app.models import Application
from sqlalchemy.ext.asyncio import AsyncSession


def _route_after_extract(state: LoanAgentState) -> str:
    return "stop" if state.get("degraded") else "continue"


def build_graph(db: AsyncSession, application: Application):
    graph = StateGraph(LoanAgentState)

    graph.add_node("ingest_route", make_ingest_route_node(db, application))
    graph.add_node("extract_trace", make_extract_trace_node(db, application))
    graph.add_node("reconcile", make_reconcile_node(db, application))
    graph.add_node("chase", make_chase_node(db, application))

    graph.add_edge(START, "ingest_route")
    graph.add_edge("ingest_route", "extract_trace")
    graph.add_conditional_edges(
        "extract_trace",
        _route_after_extract,
        {"continue": "reconcile", "stop": END},
    )
    graph.add_edge("reconcile", "chase")
    graph.add_edge("chase", END)

    return graph.compile()