# agents/writer_agent.py
# The Writer is the COMPOSER of the system.
# It takes the analyzed insights and crafts
# a polished, well-structured final response.
# Think of it as a professional writer who takes
# research notes and turns them into a great report.

import json
from typing import Optional

from agents.base_agent import BaseAgent
from message_queue import Message
from config import settings


class WriterAgent(BaseAgent):
    """
    Writer Agent — composes the final polished response.

    Responsibilities:
    1. Receive task + plan + retrieval + analysis results
    2. Use LLM to write a comprehensive final response
    3. Store the final result in Redis
    4. Stream the final output to the user
    5. Mark the task as complete
    """

    def __init__(self):
        super().__init__(
            name="WriterAgent",
            queue_name=settings.writer_queue
            # Reads from queue:writer
        )

    # -----------------------------------------------------------------------
    # MAIN PROCESS METHOD
    # -----------------------------------------------------------------------

    async def process(self, message: Message) -> Optional[Message]:
        """
        Receives all previous results, writes final response.

        Input message payload:
        {
            "task": "Research climate change and write a report",
            "plan": [...3 steps...],
            "current_step": 2,
            "results": {
                "retrieval": "...retrieved info...",
                "analysis":  "...key insights..."
            }
        }

        Output message payload (forwarded to results queue):
        {
            "task": "...",
            "final_report": "...complete polished report...",
            "status": "completed"
        }
        """
        task = message.payload.get("task", "")
        plan = message.payload.get("plan", [])
        results = message.payload.get("results", {})
        retrieved_data = results.get("retrieval", "")
        analysis = results.get("analysis", "")

        if not task:
            raise ValueError("No task found in message payload")

        if not analysis:
            raise ValueError("No analysis found to write from")

        print(f"[{self.name}] Starting writing for: {task[:80]}...")

        # --- Step 1: Get writing instructions from plan ---
        writing_instructions = self._get_step_description(plan, step_number=3)

        # --- Step 2: Stream update to user ---
        await self.stream_update(
            task_id=message.task_id,
            step="writing",
            content="Composing your final report...",
            status="in_progress"
        )

        # --- Step 3: Write the final report ---
        final_report = await self._write_report(
            task=task,
            retrieved_data=retrieved_data,
            analysis=analysis,
            instructions=writing_instructions
        )

        # --- Step 4: Store result in Redis for retrieval ---
        await self.queue.store_result(
            task_id=message.task_id,
            result={
                "task":         task,
                "final_report": final_report,
                "status":       "completed",
                "results":      results,
            }
        )

        # --- Step 5: Stream the final result to user ---
        await self.stream_update(
            task_id=message.task_id,
            step="writing",
            content=final_report,
            status="completed"
        )

        print(f"[{self.name}] Writing complete ({len(final_report)} chars)")

        # --- Step 6: Forward completion signal to results queue ---
        return Message(
            task_id=message.task_id,
            agent_name="results",       # Final destination
            payload={
                "task":         task,
                "final_report": final_report,
                "status":       "completed",
            },
            step="done"
        )

    # -----------------------------------------------------------------------
    # WRITING LOGIC
    # -----------------------------------------------------------------------

    async def _write_report(
        self,
        task: str,
        retrieved_data: str,
        analysis: str,
        instructions: str
    ) -> str:
        """
        Use LLM to compose a polished final report.

        Combines retrieved data + analysis insights into
        a well-structured, readable final response.

        Returns the complete report as a string.
        """

        system_prompt = """You are an expert professional writer and communicator.
Your job is to take research data and analysis insights and compose
a comprehensive, well-structured, and engaging final report.

When writing:
- Start with a clear executive summary
- Present information in a logical flow
- Use clear headings and structure
- Write in a professional but accessible tone
- Support claims with the data provided
- End with clear conclusions and key takeaways
- Make the report complete and self-contained

Structure your report:
# [Report Title]

## Executive Summary
## Introduction  
## Key Findings
## Detailed Analysis
## Conclusions
## Key Takeaways

Write a complete, publication-ready report."""

        user_prompt = f"""Task: {task}

Writing Instructions: {instructions}

Research Data:
{retrieved_data}

Analysis & Insights:
{analysis}

Please write a comprehensive, well-structured final report that
fully addresses the task using all the research and analysis provided."""

        report = await self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.6,    # Medium = creative but structured writing
            max_tokens=2048,
        )

        return report

    # -----------------------------------------------------------------------
    # HELPER METHODS
    # -----------------------------------------------------------------------

    def _get_step_description(
        self, plan: list, step_number: int
    ) -> str:
        """
        Extract description for a specific step from the plan.
        Returns a safe default if step not found.
        """
        for step in plan:
            if step.get("step_number") == step_number:
                return step.get("description", "")
        return "Write a comprehensive and well-structured final report"