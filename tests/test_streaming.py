# tests/test_streaming.py
import pytest
import asyncio
import json
from streaming import StreamingManager
from message_queue import message_queue


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_stream_receives_updates():
    """Stream must receive published updates"""
    await message_queue.connect()
    manager = StreamingManager()
    await manager.connect()

    task_id = "test-stream-001"
    received_events = []

    async def publish_updates():
        """Simulate agents publishing updates"""
        await asyncio.sleep(0.5)
        await message_queue.publish_stream_update(task_id, {
            "task_id": task_id,
            "agent":   "OrchestratorAgent",
            "step":    "planning",
            "content": "Creating plan...",
            "status":  "in_progress",
        })
        await asyncio.sleep(0.5)
        await message_queue.publish_stream_update(task_id, {
            "task_id": task_id,
            "agent":   "WriterAgent",
            "step":    "writing",
            "content": "Final report ready!",
            "status":  "completed",
        })

    async def collect_events():
        """Collect SSE events from stream"""
        count = 0
        async for event in manager.stream_task_updates(task_id, timeout=10.0):
            received_events.append(event)
            count += 1
            if count >= 4:  # connected + 2 updates + done
                break

    # Run publisher and collector concurrently
    await asyncio.gather(
        publish_updates(),
        collect_events(),
    )

    assert len(received_events) > 0
    # Verify SSE format
    for event in received_events:
        assert event.startswith("data: ")
        assert event.endswith("\n\n")
    print(f"test_stream_receives_updates PASSED — {len(received_events)} events")

    await manager.disconnect()
    await message_queue.disconnect()