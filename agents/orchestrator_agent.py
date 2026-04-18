# agents/orchestrator_agent.py
# The Orchestrator is the BRAIN of the system.
# It receives the raw user task, breaks it into steps,
# and assigns each step to the right agent.
# Think of it as a project manager — it doesn't do the work itself,
# it plans and delegates to specialists.

import json
from typing import Optional

from agents.base_agent import BaseAgent
from message_queue import Message
from config import settings


class OrchestratorAgent(BaseAgent):
    """
    Orchestrator Agent — plans and delegates tasks.

    Responsibilities:
    1. Receive the user's raw task
    2. Use LLM to break it into clear steps
    3. Create a structured plan
    4. Forward the plan to the Retriever agent to start execution
    5. Stream progress updates to the user
    """

    def __init__(self):
        super().__init__(
            name="OrchestratorAgent",
            queue_name=settings.orchestrator_queue
            # Reads from queue:orchestrator
        )

    # -----------------------------------------------------------------------
    # MAIN PROCESS METHOD
    # -----------------------------------------------------------------------

    async def process(self, message: Message) -> Optional[Message]:
        """
        Receives the user task and creates an execution plan.

        Input message payload:
        {
            "task": "Research climate change and write a report"
        }

        Output message payload (forwarded to Retriever):
        {
            "task": "Research climate change and write a report",
            "plan": [...list of steps...],
            "current_step": 0
        }
        """
        task = message.payload.get("task", "")

        if not task:
            raise ValueError("No task provided in message payload")

        print(f"[{self.name}] Received task: {task[:80]}...")

        # --- Step 1: Stream update to user ---
        await self.stream_update(
            task_id=message.task_id,
            step="planning",
            content=f"Analyzing your task and creating an execution plan...",
            status="in_progress"
        )

        # --- Step 2: Use LLM to break task into steps ---
        plan = await self._create_plan(task)

        print(f"[{self.name}] Created plan with {len(plan)} steps")

        # --- Step 3: Stream the plan to user ---
        plan_summary = "\n".join(
            [f"Step {i+1}: {step['title']}" for i, step in enumerate(plan)]
        )
        await self.stream_update(
            task_id=message.task_id,
            step="planning",
            content=f"Execution plan ready:\n{plan_summary}",
            status="completed"
        )

        # --- Step 4: Forward to Retriever ---
        # We send the full plan + task to the retriever
        # The retriever will execute step by step
        return Message(
            task_id=message.task_id,
            agent_name="retriever",   # Next agent to handle this
            payload={
                "task":         task,
                "plan":         plan,
                "current_step": 0,    # Start from step 0
                "results":      {},   # Will be filled by each agent
            },
            step="retrieval"
        )

    # -----------------------------------------------------------------------
    # PLAN CREATION
    # -----------------------------------------------------------------------

    async def _create_plan(self, task: str) -> list[dict]:
        """
        Use the LLM to break the user task into structured steps.

        Returns a list of step dictionaries like:
        [
            {
                "step_number": 1,
                "title": "Research Phase",
                "description": "Search for relevant information about...",
                "agent": "retriever"
            },
            ...
        ]
        """

        system_prompt = """You are an expert task planner for an AI system.
Your job is to break down complex user tasks into exactly 3 clear steps:

Step 1 - RETRIEVAL: What information needs to be gathered/researched
Step 2 - ANALYSIS: What analysis or processing needs to be done  
Step 3 - WRITING: What final output needs to be written/composed

You MUST respond with ONLY a valid JSON array. No explanation, no markdown.
No text before or after the JSON. Just the raw JSON array.

Format:
[
    {
        "step_number": 1,
        "title": "string",
        "description": "string",
        "agent": "retriever"
    },
    {
        "step_number": 2,
        "title": "string", 
        "description": "string",
        "agent": "analyzer"
    },
    {
        "step_number": 3,
        "title": "string",
        "description": "string",
        "agent": "writer"
    }
]"""

        user_prompt = f"Break down this task into steps: {task}"

        # Call LLM — base class handles retries automatically
        response = await self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,    # Low temperature = more consistent/structured
            max_tokens=1024,
        )

        # Parse the JSON response
        plan = self._parse_plan(response)
        return plan

    def _parse_plan(self, response: str) -> list[dict]:
        """
        Safely parse the LLM's JSON response into a list of steps.

        LLMs sometimes add extra text around JSON, so we try
        multiple parsing strategies before giving up.
        """
        # Strategy 1: Direct JSON parse
        try:
            plan = json.loads(response.strip())
            if isinstance(plan, list) and len(plan) > 0:
                return plan
        except json.JSONDecodeError:
            pass

        # Strategy 2: Find JSON array within the response text
        # Sometimes LLM adds explanation before/after the JSON
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            if start != -1 and end > start:
                json_str = response[start:end]
                plan = json.loads(json_str)
                if isinstance(plan, list) and len(plan) > 0:
                    return plan
        except json.JSONDecodeError:
            pass

        # Strategy 3: Fallback — return a default 3-step plan
        # This ensures the pipeline never breaks even if LLM gives bad JSON
        print(f"[{self.name}] WARNING: Could not parse LLM plan, using default")
        return [
            {
                "step_number": 1,
                "title": "Information Retrieval",
                "description": "Gather relevant information about the task",
                "agent": "retriever"
            },
            {
                "step_number": 2,
                "title": "Data Analysis",
                "description": "Analyze and process the retrieved information",
                "agent": "analyzer"
            },
            {
                "step_number": 3,
                "title": "Report Writing",
                "description": "Compose a comprehensive final response",
                "agent": "writer"
            }
        ]