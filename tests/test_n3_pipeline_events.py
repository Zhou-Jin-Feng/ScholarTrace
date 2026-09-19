"""Persisted progress must be visible before the next research operation finishes."""

import asyncio
import threading

from a2_core_fixtures import approved_plan, offline_pipeline


def test_progress_is_durable_while_following_stage_is_blocked(tmp_path):
    async def run():
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            entered = asyncio.Event()
            release = asyncio.Event()
            original = pipeline.evidence.analyzer
            observed = []

            class WaitingAnalyzer:
                async def analyze(self, **kwargs):
                    entered.set()
                    await release.wait()
                    return await original.analyze(**kwargs)

            def notify(event):
                # Notifications cannot precede the durable write used by SSE replay.
                assert event in pipeline.ledger.replay(task_id=event.task_id)
                observed.append(event)

            pipeline.evidence.analyzer = WaitingAnalyzer()
            pipeline.on_event = notify
            running = asyncio.create_task(pipeline.run(
                question="What determines retrieval?",
                plan=approved_plan(), cancel_event=threading.Event(),
            ))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                assert not running.done()
                assert [e.kind for e in observed] == [
                    "search_completed", "retrieval_completed",
                ]
                assert "verify" not in backend.events
                assert pipeline.artifacts.get_ref(
                    "artifact:research:task:core:one:result"
                ) is None
            finally:
                release.set()
                result = await asyncio.wait_for(running, timeout=5)
            assert observed == result.timeline

    asyncio.run(run())


def test_replay_notification_keeps_existing_event_identity(tmp_path):
    async def run():
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            first = await pipeline.run(
                question="What determines retrieval?",
                plan=approved_plan(), cancel_event=threading.Event(),
            )
        async with offline_pipeline(tmp_path) as (pipeline, backend):
            observed = []
            pipeline.on_event = observed.append
            second = await pipeline.run(
                question="What determines retrieval?",
                plan=approved_plan(), cancel_event=threading.Event(),
            )
            assert backend.events == []
            assert second == first
            assert observed == first.timeline
            assert pipeline.ledger.event_count(first.task_id) == len(first.timeline)

    asyncio.run(run())
