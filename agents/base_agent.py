# agents/base_agent.py
# This is the PARENT class that all agents inherit from.
# It provides shared functionality so we don't repeat code in every agent.
# Think of it as a template — every agent gets retry logic, timeout handling,
# Groq API access, and queue communication for free just by inheriting this.

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from groq import AsyncGroq

from config import settings
from message_queue import RedisMessageQueue, Message, message_queue


# ---------------------------------------------------------------------------
# BASE AGENT
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """
    Abstract Base Class for all agents in the system.

    ABC = Abstract Base Class. This means:
    - You cannot create a BaseAgent directly (it's just a template)
    - Every agent that inherits this MUST implement the `process` method
    - All shared logic lives here — retry, timeout, Groq calls, queue ops

    Inheritance example:
        class OrchestratorAgent(BaseAgent):
            async def process(self, message): ...
    """

    def __init__(self, name: str, queue_name: str):
        """
        name       : Human-readable agent name e.g. "OrchestratorAgent"
        queue_name : The Redis queue this agent reads messages from
        """
        self.name = name
        self.queue_name = queue_name

        # Groq async client — used to call the LLM
        # AsyncGroq means API calls don't block other agents
        self.groq_client = AsyncGroq(api_key=settings.groq_api_key)

        # Shared message queue instance
        self.queue: RedisMessageQueue = message_queue

        # Settings shortcuts
        self.max_retries = settings.max_retries
        self.retry_base_delay = settings.retry_base_delay
        self.timeout = settings.agent_timeout
        self.model = settings.groq_model

        print(f"[{self.name}] Initialized — listening on {self.queue_name}")

    # -----------------------------------------------------------------------
    # ABSTRACT METHOD — must be implemented by every agent
    # -----------------------------------------------------------------------

    @abstractmethod
    async def process(self, message: Message) -> Optional[Message]:
        """
        Core logic of each agent. Every agent MUST override this.

        Receives : a Message from its queue
        Returns  : a new Message to push to the next queue
                   OR None if nothing should be forwarded
        """
        pass

    # -----------------------------------------------------------------------
    # GROQ API CALL WITH RETRY + EXPONENTIAL BACKOFF
    # -----------------------------------------------------------------------

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """
        Call the Groq LLM with automatic retry and exponential backoff.

        Exponential backoff means:
        - 1st retry: wait 1 second
        - 2nd retry: wait 2 seconds
        - 3rd retry: wait 4 seconds
        This prevents hammering the API when it's having issues.

        Returns the text response from the LLM.
        Raises Exception if all retries fail.
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                print(
                    f"[{self.name}] Calling LLM "
                    f"(attempt {attempt + 1}/{self.max_retries + 1})"
                )

                # asyncio.wait_for adds a timeout to the API call
                # If Groq takes longer than self.timeout seconds → TimeoutError
                response = await asyncio.wait_for(
                    self.groq_client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user",   "content": user_prompt},
                        ],
                        temperature=temperature,
                        max_tokens=max_tokens,
                    ),
                    timeout=self.timeout,
                )

                # Extract the text content from the response
                result = response.choices[0].message.content
                print(f"[{self.name}] LLM response received ({len(result)} chars)")
                return result

            except asyncio.TimeoutError:
                last_exception = Exception(
                    f"LLM call timed out after {self.timeout}s"
                )
                print(f"[{self.name}] TIMEOUT on attempt {attempt + 1}")

            except Exception as e:
                last_exception = e
                print(f"[{self.name}] ERROR on attempt {attempt + 1}: {e}")

            # Don't wait after the last attempt
            if attempt < self.max_retries:
                # Exponential backoff: 1s, 2s, 4s, 8s...
                wait_time = self.retry_base_delay * (2 ** attempt)
                print(f"[{self.name}] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

        # All retries exhausted
        raise Exception(
            f"[{self.name}] LLM call failed after "
            f"{self.max_retries + 1} attempts. "
            f"Last error: {last_exception}"
        )

    # -----------------------------------------------------------------------
    # SAFE PROCESS — wraps process() with retry + dead letter handling
    # -----------------------------------------------------------------------

    async def safe_process(self, message: Message) -> Optional[Message]:
        """
        Wraps the agent's process() method with:
        1. Retry logic (retry up to max_retries times)
        2. Dead letter queue (if all retries fail, message goes to DLQ)
        3. Timeout per attempt

        This means individual agents don't need to worry about
        failure handling — it's all handled here automatically.
        """
        last_exception = None

        for attempt in range(self.max_retries + 1):
            try:
                print(
                    f"[{self.name}] Processing message {message.message_id[:8]} "
                    f"(attempt {attempt + 1})"
                )

                # Run process() with a timeout
                result = await asyncio.wait_for(
                    self.process(message),
                    timeout=self.timeout,
                )

                print(f"[{self.name}] Successfully processed message")
                return result

            except asyncio.TimeoutError:
                last_exception = Exception(
                    f"Agent {self.name} timed out after {self.timeout}s"
                )
                print(f"[{self.name}] TIMEOUT on attempt {attempt + 1}")

            except Exception as e:
                last_exception = e
                print(f"[{self.name}] ERROR on attempt {attempt + 1}: {e}")

            # Update retry count on the message
            message.retry_count = attempt + 1

            # Wait before retrying (exponential backoff)
            if attempt < self.max_retries:
                wait_time = self.retry_base_delay * (2 ** attempt)
                print(f"[{self.name}] Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)

        # All retries exhausted — send to Dead Letter Queue
        print(
            f"[{self.name}] All retries exhausted for message "
            f"{message.message_id[:8]}. Sending to DLQ."
        )
        await self.queue.send_to_dead_letter(
            message,
            reason=str(last_exception)
        )
        return None

    # -----------------------------------------------------------------------
    # RUN LOOP — continuously listens for messages
    # -----------------------------------------------------------------------

    async def run(self):
        """
        Main agent loop. Runs forever, waiting for messages.

        Flow:
        1. Connect to Redis
        2. Wait for a message in our queue
        3. Process it with safe_process()
        4. Push result to next queue
        5. Repeat

        This runs as an async task so multiple agents
        can run concurrently without blocking each other.
        """
        print(f"[{self.name}] Starting run loop...")
        await self.queue.connect()

        while True:
            try:
                # Wait for a message (blocks up to 5 seconds, then loops)
                message = await self.queue.pop(
                    self.queue_name,
                    timeout=5.0
                )

                if message is None:
                    # No message yet — just loop and wait again
                    continue

                print(f"[{self.name}] Received message: {message}")

                # Process with full retry + DLQ protection
                result = await self.safe_process(message)

                # If process() returned a new message, push it forward
                if result is not None:
                    await self._forward_result(result)

            except asyncio.CancelledError:
                # The run loop was cancelled (app is shutting down)
                print(f"[{self.name}] Shutting down...")
                break

            except Exception as e:
                # Unexpected error in the run loop itself
                # Log it but keep running — don't crash the agent
                print(f"[{self.name}] Unexpected run loop error: {e}")
                await asyncio.sleep(1)

    async def _forward_result(self, message: Message):
        """
        Push a processed result message to the next queue.
        The queue name is determined by the agent_name in the message.

        Agent name → queue mapping:
        orchestrator → queue:orchestrator
        retriever    → queue:retriever
        analyzer     → queue:analyzer
        writer       → queue:writer
        results      → queue:results
        """
        # Map agent names to their queue names
        queue_map = {
            "orchestrator": settings.orchestrator_queue,
            "retriever":    settings.retriever_queue,
            "analyzer":     settings.analyzer_queue,
            "writer":       settings.writer_queue,
            "results":      settings.results_queue,
        }

        target_queue = queue_map.get(message.agent_name)

        if target_queue:
            await self.queue.push(target_queue, message)
            print(
                f"[{self.name}] Forwarded result to {target_queue}"
            )
        else:
            print(
                f"[{self.name}] WARNING: Unknown target agent "
                f"'{message.agent_name}' — message not forwarded"
            )

    # -----------------------------------------------------------------------
    # HELPER — publish streaming update to user
    # -----------------------------------------------------------------------

    async def stream_update(
        self,
        task_id: str,
        step: str,
        content: str,
        status: str = "in_progress"
    ):
        """
        Send a real-time update to the user while processing.

        This is called by each agent as it completes its work,
        so the user sees progress instead of waiting for everything.

        status options: "in_progress", "completed", "failed"
        """
        update = {
            "task_id":   task_id,
            "agent":     self.name,
            "step":      step,
            "content":   content,
            "status":    status,
            "timestamp": time.time(),
        }
        await self.queue.publish_stream_update(task_id, update)
        print(f"[{self.name}] Streamed update for step: {step}")