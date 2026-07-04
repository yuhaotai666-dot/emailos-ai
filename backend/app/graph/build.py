"""Compile the per-email agent into a LangGraph ``StateGraph``.

Graph shape (one run == one email)::

    START ─► classify ─┬─(needs_reply?)─► retrieve ─► generate ─► evaluate ─┐
                       │                                                    │
                       └─(no)────────────────────────────────► END         │
                                                                           │
        evaluate ─(should_rewrite & attempts left?)─► rewrite ─► evaluate  │
        evaluate ─(good enough / out of retries)───► finalize ─► END ◄──────┘

Classification, retrieval, drafting, critique, and scoring are delegated to the
services; the deterministic constraint check runs inside ``evaluate``. Nothing
here sends email.
"""
from __future__ import annotations

from dataclasses import dataclass

from langgraph.graph import END, START, StateGraph

from .nodes import EmailNodes
from .state import EmailState


@dataclass
class EmailGraphDeps:
    classifier: object
    retriever: object
    generator: object
    critic: object
    scorer: object


def build_email_graph(deps: EmailGraphDeps):
    """Build and compile the per-email StateGraph."""
    nodes = EmailNodes(deps)
    g: StateGraph = StateGraph(EmailState)

    g.add_node("classify", nodes.classify)
    g.add_node("retrieve", nodes.retrieve)
    g.add_node("generate", nodes.generate)
    g.add_node("evaluate", nodes.evaluate)
    g.add_node("rewrite", nodes.rewrite)
    g.add_node("finalize", nodes.finalize)

    g.add_edge(START, "classify")
    g.add_conditional_edges(
        "classify",
        nodes.route_after_classify,
        {"retrieve": "retrieve", "__end__": END},
    )
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "evaluate")
    g.add_conditional_edges(
        "evaluate",
        nodes.route_after_evaluate,
        {"rewrite": "rewrite", "finalize": "finalize"},
    )
    g.add_edge("rewrite", "evaluate")
    g.add_edge("finalize", END)

    return g.compile()
