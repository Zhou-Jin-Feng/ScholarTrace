"""Loopback-only synthetic SSE host for the N4 browser stream soak test.

The host deliberately models durable event replay and controlled transport
cuts. It does not import provider clients or create research tasks.
"""
# The embedded browser harness is intentionally compact; its JavaScript lines
# are not Python source and are excluded from Python line-length formatting.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import asyncio
import json

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse

EVENTS = [
    ("task_created", "workspace"),
    ("plan_generated", "planning"),
    ("plan_approved", "planning"),
    ("search_completed", "search"),
    ("retrieval_completed", "retrieval"),
    ("verification_completed", "verification"),
    ("synthesis_completed", "synthesis"),
    ("exports_ready", "delivery"),
]


def event_frame(sequence: int, kind: str, node: str) -> str:
    payload = {"sequence": sequence, "kind": kind, "node": node,
               "created_at": "2026-09-18T00:00:00Z", "payload": {"synthetic": True}}
    return (f"id: event:{sequence}\nevent: {kind}\n"
            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n")


def create_app() -> FastAPI:
    app = FastAPI()
    app.state.connections = []

    @app.get("/health/live")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/")
    async def index() -> HTMLResponse:
        return HTMLResponse("""<!doctype html><meta charset=utf-8>
<title>N4 SSE harness</title><main><h1>事件流长时验证</h1>
<p id=status>idle</p><p id=last>0</p><p id=events></p>
<button id=reconnect>重新连接</button><button id=switch>切换任务</button>
<script>
const state={task:'task-a', source:null, last:0, events:[], attempts:0};
state.mode=new URLSearchParams(location.search).get('mode')||'none';
const status=document.querySelector('#status'), last=document.querySelector('#last'), list=document.querySelector('#events');
function draw(s){status.textContent=s; last.textContent=String(state.last); list.textContent=state.events.join(',');}
function connect(){if(state.source) state.source.close(); draw('connecting');
 state.source=new EventSource('/proxy/api/v1/research/tasks/'+state.task+'/events?follow=true&cut='+state.mode+'&last_event_id=event:'+state.last);
 state.source.onopen=()=>draw('open');
 ['task_created','plan_generated','plan_approved','search_completed','retrieval_completed','verification_completed','synthesis_completed','exports_ready'].forEach(k=>state.source.addEventListener(k,e=>{const x=JSON.parse(e.data);if(x.sequence>state.last){state.last=x.sequence;state.events.push(k);draw(k==='exports_ready'?'closed':'open');if(k==='exports_ready')state.source.close()}}));
 state.source.onerror=()=>{state.source.close();state.attempts++;draw(state.attempts>5?'error':'reconnecting');if(state.attempts<=5)setTimeout(connect,[1000,2000,4000,8000,15000][state.attempts-1]);};
}
document.querySelector('#reconnect').onclick=()=>{state.mode='none';state.attempts=0;connect()};
document.querySelector('#switch').onclick=()=>{state.task=state.task==='task-a'?'task-b':'task-a';state.last=0;state.events=[];state.attempts=0;connect()};
window.addEventListener('beforeunload',()=>state.source?.close()); connect();
</script></main>""")

    @app.get("/proxy/api/v1/research/tasks/{task_id}/events")
    async def events(request: Request, task_id: str, follow: bool = False,
                     cut: str = "none", last_event_id: str | None = None):
        del follow
        start = int((last_event_id or "event:0").split(":")[-1])
        app.state.connections.append({"task": task_id, "cut": cut, "start": start})

        async def stream():
            for sequence, (kind, node) in enumerate(EVENTS, start=1):
                if sequence <= start:
                    continue
                await asyncio.sleep(0.35)
                # short cuts after two durable events; long cuts after the
                # sixth, forcing the bounded retry window to be exceeded.
                short_cuts = sum(1 for item in app.state.connections
                                 if item["task"] == task_id and item["cut"] == "short")
                long_cuts = sum(1 for item in app.state.connections
                                if item["task"] == task_id and item["cut"] == "long")
                if (cut == "short" and short_cuts <= 3 and sequence == 3) or (
                    cut == "long" and long_cuts <= 6 and sequence == 7
                ):
                    return
                yield event_frame(sequence, kind, node)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache, no-transform",
                                          "X-Accel-Buffering": "no"})

    @app.get("/audit")
    async def audit() -> dict[str, object]:
        return {"connections": app.state.connections}

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18766)
    args = parser.parse_args()
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, access_log=False)
