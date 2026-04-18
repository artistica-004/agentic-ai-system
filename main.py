# main.py
# The FastAPI application — entry point for the entire system.
# Exposes two endpoints:
#   POST /task    — submit a new task
#   GET  /stream/{task_id} — stream real-time progress
#   GET  /result/{task_id} — get final result
#   GET  /health           — check system health

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pipeline import pipeline
from streaming import streaming_manager
from message_queue import message_queue
from config import settings


# ---------------------------------------------------------------------------
# STARTUP & SHUTDOWN
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan manager — runs on startup and shutdown.
    asynccontextmanager means code before yield = startup,
    code after yield = shutdown.
    """
    # STARTUP
    print("[App] Starting up Agentic AI System...")
    await streaming_manager.connect()
    await pipeline.start()
    print("[App] System ready!")

    yield  # App runs here

    # SHUTDOWN
    print("[App] Shutting down...")
    await pipeline.stop()
    await streaming_manager.disconnect()
    print("[App] Goodbye!")


# ---------------------------------------------------------------------------
# FASTAPI APP
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Agentic AI System",
    description="Multi-agent async pipeline for complex task processing",
    version="1.0.0",
    lifespan=lifespan,
)

# Allow browser requests from any origin (needed for frontend)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# REQUEST/RESPONSE MODELS
# ---------------------------------------------------------------------------

class TaskRequest(BaseModel):
    """Request body for submitting a task."""
    task: str

    class Config:
        json_schema_extra = {
            "example": {
                "task": "Research climate change and write a detailed report"
            }
        }


class TaskResponse(BaseModel):
    """Response after submitting a task."""
    task_id: str
    status: str
    message: str
    stream_url: str


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Returns system status — useful for monitoring.
    """
    try:
        # Check Redis connection
        await message_queue.redis.ping()
        redis_status = "connected"
    except Exception:
        redis_status = "disconnected"

    return {
        "status":       "healthy" if redis_status == "connected" else "degraded",
        "redis":        redis_status,
        "agents":       ["orchestrator", "retriever", "analyzer", "writer"],
        "pipeline":     "running" if pipeline._running else "stopped",
    }


@app.post("/task", response_model=TaskResponse)
async def submit_task(request: TaskRequest):
    """
    Submit a new task to the agentic pipeline.

    The task will be:
    1. Sent to the Orchestrator for planning
    2. Processed by Retriever, Analyzer, Writer in sequence
    3. Results streamed back via /stream/{task_id}

    Returns task_id to track progress.
    """
    if not request.task.strip():
        raise HTTPException(
            status_code=400,
            detail="Task cannot be empty"
        )

    if len(request.task) > 2000:
        raise HTTPException(
            status_code=400,
            detail="Task too long (max 2000 characters)"
        )

    try:
        # Submit task to pipeline
        task_id = await pipeline.submit_task(request.task)

        return TaskResponse(
            task_id=task_id,
            status="submitted",
            message="Task submitted successfully. Connect to stream_url for live updates.",
            stream_url=f"/stream/{task_id}",
        )

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to submit task: {str(e)}"
        )


@app.get("/stream/{task_id}")
async def stream_task(task_id: str):
    """
    Stream real-time progress updates for a task.

    Returns a Server-Sent Events (SSE) stream.
    Connect to this from your frontend to see live progress.

    Each event is JSON formatted:
    {
        "task_id": "...",
        "agent": "RetrieverAgent",
        "step": "retrieval",
        "content": "...",
        "status": "in_progress" | "completed" | "failed"
    }
    """
    if not task_id:
        raise HTTPException(status_code=400, detail="task_id required")

    return StreamingResponse(
        streaming_manager.stream_task_updates(task_id),
        media_type="text/event-stream",
        headers={
            # Prevent proxy/browser from buffering the stream
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        }
    )


@app.get("/result/{task_id}")
async def get_result(task_id: str):
    """
    Get the final result of a completed task.

    Returns the complete report once the Writer agent finishes.
    If the task is still running, returns a 404.
    """
    result = await message_queue.get_result(task_id)

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Result not found. Task may still be processing."
        )

    return {
        "task_id": task_id,
        "status":  result.get("status", "unknown"),
        "task":    result.get("task", ""),
        "report":  result.get("final_report", ""),
    }


@app.get("/dead-letter")
async def get_dead_letter_messages():
    """
    Get all messages that failed after max retries.
    Useful for debugging failures.
    """
    messages = await message_queue.get_dead_letter_messages()
    return {
        "count": len(messages),
        "messages": [msg.to_dict() for msg in messages],
    }


@app.delete("/queues")
async def clear_all_queues():
    """
    Clear all Redis queues.
    Use this to reset the system during testing.
    """
    await message_queue.clear_all_queues()
    return {"status": "cleared", "message": "All queues cleared"}