# streaming.py
# Handles real-time streaming of agent progress to the user.
# Uses Server-Sent Events (SSE) — a simple protocol where
# the server pushes updates to the browser over HTTP.
# The user sees live progress instead of waiting for everything.

import asyncio
import json
import time
from typing import AsyncGenerator

import redis.asyncio as aioredis

from config import REDIS_URL, settings
from message_queue import message_queue


class StreamingManager:
    """
    Manages Server-Sent Events (SSE) streaming.

    How SSE works:
    1. Client opens a GET /stream/{task_id} connection
    2. Server keeps the connection open
    3. Server pushes text events as agents complete steps
    4. Client receives updates in real time
    5. Connection closes when task is done or client disconnects

    Why SSE over WebSocket?
    - Simpler — one-way communication (server → client) is all we need
    - Works over regular HTTP — no special protocol
    - Auto-reconnects if connection drops
    - Perfect for progress updates
    """

    def __init__(self):
        # Separate Redis connection for pub/sub
        # (pub/sub needs its own dedicated connection)
        self.pubsub_redis: aioredis.Redis = None

    async def connect(self):
        """Create dedicated Redis connection for pub/sub."""
        self.pubsub_redis = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

    async def disconnect(self):
        """Close pub/sub Redis connection."""
        if self.pubsub_redis:
            await self.pubsub_redis.aclose()

    # -----------------------------------------------------------------------
    # SSE EVENT GENERATOR
    # -----------------------------------------------------------------------

    async def stream_task_updates(
        self,
        task_id: str,
        timeout: float = 120.0
    ) -> AsyncGenerator[str, None]:
        """
        AsyncGenerator that yields SSE-formatted events.

        FastAPI uses this as a streaming response — it keeps
        yielding strings to the client until the task completes.

        SSE format (must follow this exactly):
            data: {"key": "value"}\n\n

        The double newline \n\n signals end of one event.
        """
        print(f"[Streaming] Starting stream for task {task_id[:8]}")

        # Create fresh Redis connection for this stream
        redis_conn = await aioredis.from_url(
            REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )

        # Subscribe to the task's Redis channel
        # Agents publish to: stream:{task_id}
        pubsub = redis_conn.pubsub()
        channel = f"stream:{task_id}"
        await pubsub.subscribe(channel)

        # Send initial connection event to client
        yield self._format_sse({
            "task_id": task_id,
            "status":  "connected",
            "message": "Connected to task stream. Processing started...",
            "agent":   "system",
            "step":    "init",
        })

        start_time = time.time()
        completed = False

        try:
            # Keep listening for messages until done or timeout
            while not completed:

                # Check timeout
                if time.time() - start_time > timeout:
                    yield self._format_sse({
                        "task_id": task_id,
                        "status":  "timeout",
                        "message": "Task timed out. Please try again.",
                        "agent":   "system",
                        "step":    "timeout",
                    })
                    break

                # Get next message from pub/sub (non-blocking)
                try:
                    raw_message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    # No message in last 1 second — send heartbeat
                    # Heartbeat keeps the HTTP connection alive
                    yield self._format_sse({
                        "type": "heartbeat",
                        "timestamp": time.time()
                    })
                    continue

                if raw_message is None:
                    # No message yet — small wait and retry
                    await asyncio.sleep(0.1)
                    continue

                # Parse the message data
                try:
                    update = json.loads(raw_message["data"])
                except (json.JSONDecodeError, KeyError):
                    continue

                # Forward the update to the client as SSE
                yield self._format_sse(update)

                # Check if this is the final completion event
                if (
                    update.get("status") == "completed"
                    and update.get("step") == "writing"
                ):
                    # Send a final done event
                    yield self._format_sse({
                        "task_id": task_id,
                        "status":  "done",
                        "message": "Task completed successfully!",
                        "agent":   "system",
                        "step":    "done",
                    })
                    completed = True

                # Check for failure
                if update.get("status") == "failed":
                    yield self._format_sse({
                        "task_id": task_id,
                        "status":  "failed",
                        "message": "Task failed. Check logs for details.",
                        "agent":   "system",
                        "step":    "error",
                    })
                    completed = True

        finally:
            # Always clean up pub/sub subscription
            await pubsub.unsubscribe(channel)
            await redis_conn.aclose()
            print(f"[Streaming] Stream closed for task {task_id[:8]}")

    # -----------------------------------------------------------------------
    # SSE FORMATTER
    # -----------------------------------------------------------------------

    def _format_sse(self, data: dict) -> str:
        """
        Format a dictionary as a proper SSE event string.

        SSE protocol requires:
        - Lines starting with 'data: '
        - Double newline at the end to signal event boundary

        Example output:
            data: {"status": "in_progress", "agent": "RetrieverAgent"}

        """
        json_str = json.dumps(data)
        return f"data: {json_str}\n\n"


# Single shared streaming manager instance
streaming_manager = StreamingManager()