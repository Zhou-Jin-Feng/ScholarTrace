from __future__ import annotations

import asyncio
import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt


class FanoutState(TypedDict):
    items: list[str]
    results: Annotated[list[str], operator.add]


class WorkerState(TypedDict):
    item: str


def _dispatch(state: FanoutState) -> list[Send]:
    return [Send("worker", {"item": item}) for item in state["items"]]


def _worker(state: WorkerState) -> dict[str, list[str]]:
    return {"results": [state["item"].upper()]}


def test_send_parallel_reducer_smoke() -> None:
    builder = StateGraph(FanoutState)
    builder.add_node("worker", _worker)
    builder.add_conditional_edges(START, _dispatch, ["worker"])
    builder.add_edge("worker", END)
    graph = builder.compile()

    result = graph.invoke({"items": ["paper-a", "paper-b"], "results": []})

    assert sorted(result["results"]) == ["PAPER-A", "PAPER-B"]


class ApprovalState(TypedDict):
    proposal: str
    approved: bool


def _approval_node(state: ApprovalState) -> dict[str, bool]:
    decision = interrupt({"proposal": state["proposal"]})
    return {"approved": bool(decision)}


def test_interrupt_resume_and_checkpoint_smoke() -> None:
    builder = StateGraph(ApprovalState)
    builder.add_node("approval", _approval_node)
    builder.add_edge(START, "approval")
    builder.add_edge("approval", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "m0-approval-smoke"}}

    first = graph.invoke({"proposal": "approve M0", "approved": False}, config)
    assert "__interrupt__" in first

    resumed = graph.invoke(Command(resume=True), config)
    snapshot = graph.get_state(config)

    assert resumed["approved"] is True
    assert snapshot.values["approved"] is True


def test_event_stream_smoke() -> None:
    builder = StateGraph(FanoutState)
    builder.add_node("worker", _worker)
    builder.add_conditional_edges(START, _dispatch, ["worker"])
    builder.add_edge("worker", END)
    graph = builder.compile()

    async def collect_events() -> list[str]:
        return [
            event["event"]
            async for event in graph.astream_events(
                {"items": ["paper-a"], "results": []}, version="v2"
            )
        ]

    event_names = asyncio.run(collect_events())
    assert "on_chain_start" in event_names
    assert "on_chain_end" in event_names
