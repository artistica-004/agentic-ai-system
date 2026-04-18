# pipeline.py
# The Pipeline coordinates all agents in the correct sequence.
# It starts all agents as concurrent async tasks,
# pushes the initial message to kick off the flow,
# and monitors progress until completion.

import asyncio
import uuid
from typing import Optional

from agents.orchestrator_agent import OrchestratorAgent
from agents.retriever_agent import RetrieverAgent
from agents.analyzer_agent import AnalyzerAgent
from agents.writer_agent import WriterAgent
from message_queue import Message, message_queue
from config import settings


class Pipeline:
    """
    Async Pipeline that coordinates all agents.

    Flow:
    User Task → Orchestrator → Retriever → Analyzer → Writer → Result

    Each agent runs as a separate async task (concurrently).
    They communicate via Redis queues — not direct calls.
    This means agents are fully decoupled from each other.
    """

    def __init__(self):
        # Create all agent instances
        self.orchestrator = OrchestratorAgent()
        self.retriever = RetrieverAgent()
        self.analyzer = AnalyzerAgent()
        self.writer = WriterAgent()

        # Track running agent tasks
        self._agent_tasks: list[asyncio.Task] = []
        self._running = False

    # -----------------------------------------------------------------------
    # START ALL AGENTS
    # -----------------------------------------------------------------------

    async def start(self):
        """
        Connect to Redis and start all agents as concurrent async tasks.
        Each agent runs in its own task — listening to its queue forever.
        """
        print("[Pipeline] Starting up...")
        await message_queue.connect()

        # Start each agent as a background async task
        # asyncio.create_task() runs them concurrently
        self._agent_tasks = [
            asyncio.create_task(
                self.orchestrator.run(),
                name="orchestrator-task"
            ),
            asyncio.create_task(
                self.retriever.run(),
                name="retriever-task"
            ),
            asyncio.create_task(
                self.analyzer.run(),
                name="analyzer-task"
            ),
            asyncio.create_task(
                self.writer.run(),
                name="writer-task"
            ),
        ]

        self._running = True
        print("[Pipeline] All agents running!")

    # -----------------------------------------------------------------------
    # STOP ALL AGENTS
    # -----------------------------------------------------------------------

    async def stop(self):
        """
        Gracefully stop all agent tasks and disconnect from Redis.
        Called when the FastAPI app shuts down.
        """
        print("[Pipeline] Shutting down...")
        self._running = False

        # Cancel all running agent tasks
        for task in self._agent_tasks:
            task.cancel()

        # Wait for all tasks to finish cancelling
        await asyncio.gather(*self._agent_tasks, return_exceptions=True)

        await message_queue.disconnect()
        print("[Pipeline] Shutdown complete")

    # -----------------------------------------------------------------------
    # SUBMIT TASK
    # -----------------------------------------------------------------------

    async def submit_task(self, task: str) -> str:
        """
        Submit a new user task into the pipeline.

        Creates a unique task_id, wraps the task in a Message,
        and pushes it to the Orchestrator queue to begin processing.

        Returns the task_id so the user can poll/stream results.
        """
        # Generate unique ID for this task
        task_id = str(uuid.uuid4())

        print(f"[Pipeline] Submitting task {task_id[:8]}: {task[:80]}...")

        # Create initial message
        message = Message(
            task_id=task_id,
            agent_name="orchestrator",
            payload={"task": task},
            step="start"
        )

        # Push to orchestrator queue to begin the pipeline
        success = await message_queue.push(
            settings.orchestrator_queue,
            message
        )

        if not success:
            raise Exception("Failed to submit task to pipeline")

        print(f"[Pipeline] Task {task_id[:8]} submitted successfully")
        return task_id

    # -----------------------------------------------------------------------
    # WAIT FOR RESULT
    # -----------------------------------------------------------------------

    async def get_result(
        self,
        task_id: str,
        timeout: float = 120.0
    ) -> Optional[dict]:
        """
        Poll Redis until the task result is available or timeout.

        The Writer agent stores the final result in Redis when done.
        We check every second until it appears or we timeout.

        Returns the result dict or None if timed out.
        """
        print(f"[Pipeline] Waiting for result of task {task_id[:8]}...")

        start_time = asyncio.get_event_loop().time()

        while True:
            # Check if result is available in Redis
            result = await message_queue.get_result(task_id)

            if result is not None:
                print(f"[Pipeline] Result ready for task {task_id[:8]}")
                return result

            # Check if we've exceeded the timeout
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                print(f"[Pipeline] Timeout waiting for task {task_id[:8]}")
                return None

            # Wait 1 second before checking again
            await asyncio.sleep(1.0)

    # -----------------------------------------------------------------------
    # RUN TASK END TO END (for testing without HTTP)
    # -----------------------------------------------------------------------

    async def run_task(self, task: str) -> Optional[dict]:
        """
        Submit a task and wait for its result.
        Useful for testing the full pipeline without FastAPI.

        Returns the complete result dict.
        """
        task_id = await self.submit_task(task)
        result = await self.get_result(task_id)
        return result


# Single shared pipeline instance used by FastAPI
pipeline = Pipeline()