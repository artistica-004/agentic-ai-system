# tests/test_pipeline.py
import pytest
import asyncio
from pipeline import Pipeline
from message_queue import message_queue


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.mark.asyncio
async def test_full_pipeline():
    """Test complete pipeline from task submission to final result"""
    p = Pipeline()
    await p.start()

    # Give agents a moment to initialize
    await asyncio.sleep(1)

    # Submit a task
    task_id = await p.submit_task(
        "Research the benefits of exercise and write a short report"
    )

    assert task_id is not None
    print(f"Task submitted: {task_id[:8]}")

    # Wait for result (up to 120 seconds)
    result = await p.get_result(task_id, timeout=120.0)

    assert result is not None, "Pipeline timed out — no result received"
    assert result["status"] == "completed"
    assert "final_report" in result
    assert len(result["final_report"]) > 200

    print(f"Pipeline test PASSED!")
    print(f"Report preview: {result['final_report'][:300]}...")

    await p.stop()


@pytest.mark.asyncio
async def test_task_submission_returns_id():
    """Task submission must return a valid UUID"""
    p = Pipeline()
    await p.start()
    await asyncio.sleep(1)

    task_id = await p.submit_task("Write a haiku about Python")
    assert task_id is not None
    assert len(task_id) == 36  # UUID format
    print(f"test_task_submission_returns_id PASSED: {task_id[:8]}")

    await p.stop()