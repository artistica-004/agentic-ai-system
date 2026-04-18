# agents/retriever_agent.py
# The Retriever is the RESEARCHER of the system.
# It receives the task + plan from the Orchestrator,
# and gathers all relevant information needed for the task.
# Think of it as a research assistant — it goes and finds
# everything needed before any analysis or writing begins.

import json
from typing import Optional

from agents.base_agent import BaseAgent
from message_queue import Message
from config import settings


class RetrieverAgent(BaseAgent):
    """
    Retriever Agent — gathers and fetches relevant information.

    Responsibilities:
    1. Receive task + plan from Orchestrator
    2. Use LLM to simulate research/retrieval of relevant information
    3. Structure the retrieved data clearly
    4. Forward everything to the Analyzer agent
    5. Stream progress to user in real time
    """

    def __init__(self):
        super().__init__(
            name="RetrieverAgent",
            queue_name=settings.retriever_queue
            # Reads from queue:retriever
        )

    # -----------------------------------------------------------------------
    # MAIN PROCESS METHOD
    # -----------------------------------------------------------------------

    async def process(self, message: Message) -> Optional[Message]:
        """
        Receives task + plan, retrieves relevant information.

        Input message payload:
        {
            "task": "Research climate change and write a report",
            "plan": [...3 steps...],
            "current_step": 0,
            "results": {}
        }

        Output message payload (forwarded to Analyzer):
        {
            "task": "...",
            "plan": [...],
            "current_step": 1,
            "results": {
                "retrieval": "...all retrieved information..."
            }
        }
        """
        task = message.payload.get("task", "")
        plan = message.payload.get("plan", [])
        results = message.payload.get("results", {})

        if not task:
            raise ValueError("No task found in message payload")

        print(f"[{self.name}] Starting retrieval for: {task[:80]}...")

        # --- Step 1: Get retrieval instructions from plan ---
        # Find the retriever step in the plan
        retrieval_step = self._get_step_description(plan, step_number=1)

        # --- Step 2: Stream update to user ---
        await self.stream_update(
            task_id=message.task_id,
            step="retrieval",
            content=f"Researching and gathering information...\n{retrieval_step}",
            status="in_progress"
        )

        # --- Step 3: Retrieve information using LLM ---
        retrieved_data = await self._retrieve_information(task, retrieval_step)

        # --- Step 4: Stream completion update ---
        await self.stream_update(
            task_id=message.task_id,
            step="retrieval",
            content=f"Information gathering complete. Found relevant data.",
            status="completed"
        )

        # --- Step 5: Add retrieved data to results ---
        results["retrieval"] = retrieved_data

        print(f"[{self.name}] Retrieval complete ({len(retrieved_data)} chars)")

        # --- Step 6: Forward to Analyzer ---
        return Message(
            task_id=message.task_id,
            agent_name="analyzer",      # Next agent
            payload={
                "task":         task,
                "plan":         plan,
                "current_step": 1,      # Move to step 1 (analyzer)
                "results":      results,
            },
            step="analysis"
        )

    # -----------------------------------------------------------------------
    # INFORMATION RETRIEVAL
    # -----------------------------------------------------------------------

    async def _retrieve_information(
        self, task: str, retrieval_instructions: str
    ) -> str:
        """
        Use LLM to gather comprehensive information about the task.

        In a production system, this would call real APIs like:
        - Google Search API
        - Wikipedia API
        - News APIs
        - Academic paper databases

        For this system, we use the LLM's knowledge base to simulate
        thorough research and information gathering.

        Returns a structured string of retrieved information.
        """

        system_prompt = """You are an expert research assistant with access to 
vast knowledge across all domains. Your job is to gather comprehensive, 
accurate, and well-structured information about any given topic.

When retrieving information:
- Cover multiple perspectives and aspects of the topic
- Include key facts, statistics, and important details
- Organize information in clear sections
- Be thorough but focused on what's most relevant
- Write as if you have just researched this topic deeply

Structure your response with clear sections using headers like:
## Overview
## Key Facts  
## Important Details
## Recent Developments
## Relevant Statistics

Be comprehensive — the analyzer will use this to draw insights."""

        user_prompt = f"""Task: {task}

Retrieval Instructions: {retrieval_instructions}

Please gather and present all relevant information needed to complete this task.
Be thorough and comprehensive in your research."""

        retrieved = await self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.4,    # Slightly low = factual and consistent
            max_tokens=2048,    # Allow long detailed retrieval
        )

        return retrieved

    # -----------------------------------------------------------------------
    # HELPER METHODS
    # -----------------------------------------------------------------------

    def _get_step_description(
        self, plan: list, step_number: int
    ) -> str:
        """
        Extract the description for a specific step from the plan.

        If the plan doesn't have that step number,
        returns a sensible default so the pipeline never breaks.
        """
        for step in plan:
            if step.get("step_number") == step_number:
                return step.get("description", "")

        # Default fallback if step not found
        return "Gather all relevant information about the topic"