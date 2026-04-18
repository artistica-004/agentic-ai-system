# agents/analyzer_agent.py
# The Analyzer is the THINKER of the system.
# It takes raw retrieved information and extracts
# key insights, patterns, and structured findings.
# Think of it as a senior analyst who reads research
# and pulls out what actually matters.

import json
from typing import Optional

from agents.base_agent import BaseAgent
from message_queue import Message
from config import settings


class AnalyzerAgent(BaseAgent):
    """
    Analyzer Agent — processes and extracts insights from retrieved data.

    Responsibilities:
    1. Receive task + plan + retrieved data from Retriever
    2. Use LLM to analyze and extract key insights
    3. Structure findings clearly for the Writer
    4. Forward everything to the Writer agent
    5. Stream progress to user in real time
    """

    def __init__(self):
        super().__init__(
            name="AnalyzerAgent",
            queue_name=settings.analyzer_queue
            # Reads from queue:analyzer
        )

    # -----------------------------------------------------------------------
    # MAIN PROCESS METHOD
    # -----------------------------------------------------------------------

    async def process(self, message: Message) -> Optional[Message]:
        """
        Receives retrieved data, analyzes it, extracts insights.

        Input message payload:
        {
            "task": "Research climate change and write a report",
            "plan": [...3 steps...],
            "current_step": 1,
            "results": {
                "retrieval": "...all retrieved information..."
            }
        }

        Output message payload (forwarded to Writer):
        {
            "task": "...",
            "plan": [...],
            "current_step": 2,
            "results": {
                "retrieval": "...",
                "analysis": "...key insights and findings..."
            }
        }
        """
        task = message.payload.get("task", "")
        plan = message.payload.get("plan", [])
        results = message.payload.get("results", {})
        retrieved_data = results.get("retrieval", "")

        if not task:
            raise ValueError("No task found in message payload")

        if not retrieved_data:
            raise ValueError("No retrieved data found to analyze")

        print(f"[{self.name}] Starting analysis for: {task[:80]}...")

        # --- Step 1: Get analysis instructions from plan ---
        analysis_instructions = self._get_step_description(plan, step_number=2)

        # --- Step 2: Stream update to user ---
        await self.stream_update(
            task_id=message.task_id,
            step="analysis",
            content=f"Analyzing retrieved information and extracting insights...",
            status="in_progress"
        )

        # --- Step 3: Analyze the retrieved data ---
        analysis = await self._analyze_data(
            task=task,
            retrieved_data=retrieved_data,
            instructions=analysis_instructions
        )

        # --- Step 4: Stream completion ---
        await self.stream_update(
            task_id=message.task_id,
            step="analysis",
            content="Analysis complete. Key insights extracted.",
            status="completed"
        )

        # --- Step 5: Add analysis to results ---
        results["analysis"] = analysis

        print(f"[{self.name}] Analysis complete ({len(analysis)} chars)")

        # --- Step 6: Forward to Writer ---
        return Message(
            task_id=message.task_id,
            agent_name="writer",        # Next agent
            payload={
                "task":         task,
                "plan":         plan,
                "current_step": 2,      # Move to step 2 (writer)
                "results":      results,
            },
            step="writing"
        )

    # -----------------------------------------------------------------------
    # ANALYSIS LOGIC
    # -----------------------------------------------------------------------

    async def _analyze_data(
        self,
        task: str,
        retrieved_data: str,
        instructions: str
    ) -> str:
        """
        Use LLM to analyze retrieved data and extract key insights.

        Takes raw research data and produces structured analysis
        that the Writer can use to compose a final response.

        Returns structured analysis as a string.
        """

        system_prompt = """You are an expert data analyst and critical thinker.
Your job is to analyze research data and extract the most important
insights, patterns, and findings.

When analyzing:
- Identify the most important facts and findings
- Look for patterns, trends, and relationships
- Highlight contradictions or nuances
- Assess the significance of different pieces of information
- Draw logical conclusions from the data
- Identify gaps or areas needing more information

Structure your analysis using these sections:
## Key Findings
## Critical Insights  
## Important Patterns & Trends
## Conclusions
## Recommendations for Report

Be analytical and precise — the writer will use this to craft the final output."""

        user_prompt = f"""Task: {task}

Analysis Instructions: {instructions}

Retrieved Information to Analyze:
{retrieved_data}

Please analyze this information thoroughly and extract all key insights,
patterns, and findings that are most relevant to the task."""

        analysis = await self.call_llm(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.3,    # Low = precise and analytical
            max_tokens=2048,
        )

        return analysis

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
        return "Analyze the retrieved information and extract key insights"