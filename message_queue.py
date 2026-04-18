# message_queue.py
# This file is the backbone of agent communication.
# Instead of agents calling each other directly,
# they send and receive messages through Redis queues.
# Think of it like a postal system — agents drop messages
# in a mailbox and others pick them up when ready.

import asyncio
import json
import time
import uuid
from typing import Any, Optional
from datetime import datetime

import redis.asyncio as aioredis

from config import settings, REDIS_URL, DEAD_LETTER_QUEUE


# ---------------------------------------------------------------------------
# MESSAGE MODEL
# ---------------------------------------------------------------------------

class Message:
    """
    Represents a single message passed between agents.
    Every piece of data moving through the system is wrapped in this.
    """

    def __init__(
        self,
        task_id: str,           # Unique ID for the overall user task
        agent_name: str,        # Which agent sent this message
        payload: dict,          # The actual data being passed
        step: str = "",         # Which step in the pipeline this is
        retry_count: int = 0,   # How many times this message has been retried
        message_id: str = None, # Unique ID for THIS specific message
    ):
        self.task_id = task_id
        self.agent_name = agent_name
        self.payload = payload
        self.step = step
        self.retry_count = retry_count
        # Generate a unique ID if not provided
        self.message_id = message_id or str(uuid.uuid4())
        # Timestamp when message was created
        self.created_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        """Convert message to dictionary so it can be stored in Redis as JSON"""
        return {
            "task_id": self.task_id,
            "agent_name": self.agent_name,
            "payload": self.payload,
            "step": self.step,
            "retry_count": self.retry_count,
            "message_id": self.message_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        """Rebuild a Message object from a dictionary (when reading from Redis)"""
        msg = cls(
            task_id=data["task_id"],
            agent_name=data["agent_name"],
            payload=data["payload"],
            step=data.get("step", ""),
            retry_count=data.get("retry_count", 0),
            message_id=data.get("message_id"),
        )
        msg.created_at = data.get("created_at", msg.created_at)
        return msg

    def __repr__(self):
        return (
            f"Message(task_id={self.task_id}, "
            f"agent={self.agent_name}, "
            f"step={self.step}, "
            f"retries={self.retry_count})"
        )


# ---------------------------------------------------------------------------
# REDIS MESSAGE QUEUE
# ---------------------------------------------------------------------------

class RedisMessageQueue:
    """
    Async Redis-based message queue with manual batching.

    Key concepts:
    - Each agent has its own queue (like a dedicated inbox)
    - Messages are JSON-serialized and stored as Redis list items
    - LPUSH adds to left (front), BRPOP reads from right (back) = FIFO order
    - Batching: collect multiple messages and process them together
      for efficiency instead of one-by-one
    """

    def __init__(self):
        # Redis client instance — will be created when we connect
        self.redis: Optional[aioredis.Redis] = None
        # Flag to track if we're connected
        self._connected = False

    async def connect(self):
        """
        Open connection to Redis.
        Must be called before using any queue operations.
        """
        if not self._connected:
            self.redis = await aioredis.from_url(
                REDIS_URL,
                encoding="utf-8",
                decode_responses=True,   # Auto-decode bytes to strings
                max_connections=20,      # Connection pool size
            )
            self._connected = True
            print(f"[Queue] Connected to Redis at {REDIS_URL}")

    async def disconnect(self):
        """Close the Redis connection cleanly."""
        if self.redis and self._connected:
            await self.redis.aclose()
            self._connected = False
            print("[Queue] Disconnected from Redis")

    # -----------------------------------------------------------------------
    # CORE PUSH / POP OPERATIONS
    # -----------------------------------------------------------------------

    async def push(self, queue_name: str, message: Message) -> bool:
        """
        Push a single message onto a queue.

        LPUSH = Left Push = adds to the front of the Redis list.
        We read from the right (BRPOP), so this gives us FIFO order.

        Returns True if successful, False if failed.
        """
        try:
            # Serialize message to JSON string
            message_json = json.dumps(message.to_dict())
            # Push to Redis list
            await self.redis.lpush(queue_name, message_json)
            print(f"[Queue] Pushed message {message.message_id[:8]} to {queue_name}")
            return True
        except Exception as e:
            print(f"[Queue] ERROR pushing to {queue_name}: {e}")
            return False

    async def pop(
        self, queue_name: str, timeout: float = 5.0
    ) -> Optional[Message]:
        """
        Pop a single message from a queue.

        BRPOP = Blocking Right Pop = waits up to `timeout` seconds
        for a message to appear. Returns None if nothing arrives.

        This is better than a busy loop (constantly checking if empty)
        because it blocks efficiently at the Redis level.
        """
        try:
            # BRPOP returns a tuple: (queue_name, value) or None
            result = await self.redis.brpop(queue_name, timeout=timeout)
            if result is None:
                return None  # Timeout — no message arrived

            _, message_json = result
            # Deserialize JSON back into a Message object
            data = json.loads(message_json)
            return Message.from_dict(data)

        except Exception as e:
            print(f"[Queue] ERROR popping from {queue_name}: {e}")
            return None

    # -----------------------------------------------------------------------
    # MANUAL BATCHING LOGIC
    # -----------------------------------------------------------------------

    async def push_batch(self, queue_name: str, messages: list[Message]) -> int:
        """
        Push multiple messages at once using a Redis pipeline.

        A Redis pipeline sends all commands in one network round-trip
        instead of one round-trip per message. Much faster for bulk operations.

        Returns: number of messages successfully pushed.
        """
        if not messages:
            return 0

        try:
            # Use Redis pipeline for atomic batch push
            # pipeline() groups commands — execute() sends them all at once
            pipe = self.redis.pipeline()
            for message in messages:
                message_json = json.dumps(message.to_dict())
                pipe.lpush(queue_name, message_json)

            # Execute all pushes in a single network call
            await pipe.execute()
            print(f"[Queue] Batch pushed {len(messages)} messages to {queue_name}")
            return len(messages)

        except Exception as e:
            print(f"[Queue] ERROR batch pushing to {queue_name}: {e}")
            return 0

    async def pop_batch(
        self,
        queue_name: str,
        batch_size: int = None,
        timeout: float = None,
    ) -> list[Message]:
        """
        Pop up to `batch_size` messages from a queue.

        This is our MANUAL BATCHING logic:
        1. Wait for the first message (blocking wait)
        2. Then quickly grab more messages if available
        3. Stop when we hit batch_size OR when batch_timeout expires
        4. Return whatever we collected

        Why batch? Processing 5 messages together is more efficient
        than processing them one by one (less overhead per message).
        """
        batch_size = batch_size or settings.batch_size
        batch_timeout = timeout or settings.batch_timeout
        messages = []

        try:
            # --- Step 1: Wait for the FIRST message (blocking) ---
            # We wait up to batch_timeout seconds for at least one message
            result = await self.redis.brpop(queue_name, timeout=batch_timeout)
            if result is None:
                return []  # No messages arrived within timeout

            _, first_json = result
            messages.append(Message.from_dict(json.loads(first_json)))

            # --- Step 2: Greedily collect more messages (non-blocking) ---
            # After getting the first message, quickly grab more if available
            # RPOP (no blocking) — returns None immediately if queue is empty
            while len(messages) < batch_size:
                result = await self.redis.rpop(queue_name)
                if result is None:
                    break  # Queue is empty — stop collecting
                messages.append(Message.from_dict(json.loads(result)))

            print(
                f"[Queue] Batch popped {len(messages)} messages from {queue_name}"
            )
            return messages

        except Exception as e:
            print(f"[Queue] ERROR batch popping from {queue_name}: {e}")
            return []

    # -----------------------------------------------------------------------
    # DEAD LETTER QUEUE
    # -----------------------------------------------------------------------

    async def send_to_dead_letter(
        self, message: Message, reason: str
    ) -> bool:
        """
        Send a failed message to the Dead Letter Queue (DLQ).

        When a message fails after all retries, we don't just discard it.
        We move it to a special queue for inspection and debugging.
        This way we can see exactly what failed and why.
        """
        try:
            # Add failure reason and timestamp to the message payload
            message.payload["_failure_reason"] = reason
            message.payload["_failed_at"] = datetime.utcnow().isoformat()

            message_json = json.dumps(message.to_dict())
            await self.redis.lpush(DEAD_LETTER_QUEUE, message_json)
            print(
                f"[Queue] Message {message.message_id[:8]} sent to DLQ. "
                f"Reason: {reason}"
            )
            return True
        except Exception as e:
            print(f"[Queue] ERROR sending to DLQ: {e}")
            return False

    async def get_dead_letter_messages(self) -> list[Message]:
        """
        Retrieve all messages from the Dead Letter Queue for inspection.
        Useful for debugging failures.
        """
        try:
            # LRANGE gets all items in the list (0 to -1 = everything)
            raw_messages = await self.redis.lrange(DEAD_LETTER_QUEUE, 0, -1)
            messages = []
            for raw in raw_messages:
                data = json.loads(raw)
                messages.append(Message.from_dict(data))
            return messages
        except Exception as e:
            print(f"[Queue] ERROR reading DLQ: {e}")
            return []

    # -----------------------------------------------------------------------
    # UTILITY METHODS
    # -----------------------------------------------------------------------

    async def get_queue_length(self, queue_name: str) -> int:
        """Return how many messages are currently in a queue."""
        try:
            return await self.redis.llen(queue_name)
        except Exception:
            return 0

    async def clear_queue(self, queue_name: str) -> bool:
        """
        Delete all messages in a queue.
        Useful for testing and cleanup.
        """
        try:
            await self.redis.delete(queue_name)
            print(f"[Queue] Cleared queue: {queue_name}")
            return True
        except Exception as e:
            print(f"[Queue] ERROR clearing {queue_name}: {e}")
            return False

    async def clear_all_queues(self) -> bool:
        """Clear every queue in the system. Used for fresh test runs."""
        queues = [
            settings.orchestrator_queue,
            settings.retriever_queue,
            settings.analyzer_queue,
            settings.writer_queue,
            settings.results_queue,
            settings.dead_letter_queue,
        ]
        for queue in queues:
            await self.clear_queue(queue)
        print("[Queue] All queues cleared")
        return True

    async def store_result(
        self, task_id: str, result: dict, expire_seconds: int = 3600
    ) -> bool:
        """
        Store a task's final result in Redis as a key-value pair.
        Results expire after 1 hour by default to save memory.

        Key format: result:{task_id}
        """
        try:
            key = f"result:{task_id}"
            await self.redis.setex(
                key,
                expire_seconds,
                json.dumps(result)
            )
            print(f"[Queue] Stored result for task {task_id[:8]}")
            return True
        except Exception as e:
            print(f"[Queue] ERROR storing result: {e}")
            return False

    async def get_result(self, task_id: str) -> Optional[dict]:
        """Retrieve a stored task result by task_id."""
        try:
            key = f"result:{task_id}"
            raw = await self.redis.get(key)
            if raw:
                return json.loads(raw)
            return None
        except Exception as e:
            print(f"[Queue] ERROR getting result: {e}")
            return None

    async def publish_stream_update(
        self, task_id: str, update: dict
    ) -> bool:
        """
        Publish a streaming update using Redis Pub/Sub.

        This is how partial results reach the user in real-time.
        The streaming.py file subscribes to this channel and
        forwards updates to the user via SSE.

        Channel format: stream:{task_id}
        """
        try:
            channel = f"stream:{task_id}"
            await self.redis.publish(channel, json.dumps(update))
            return True
        except Exception as e:
            print(f"[Queue] ERROR publishing stream update: {e}")
            return False


# ---------------------------------------------------------------------------
# SINGLE SHARED INSTANCE
# ---------------------------------------------------------------------------
# All files import this one object — connection is shared across the app
message_queue = RedisMessageQueue()