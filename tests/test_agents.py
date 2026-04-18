# tests/test_agents.py
import pytest
import asyncio
from unittest.mock import AsyncMock, patch
from agents.orchestrator_agent import OrchestratorAgent
from agents.retriever_agent import RetrieverAgent
from agents.analyzer_agent import AnalyzerAgent
from agents.writer_agent import WriterAgent
from message_queue import Message, message_queue


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def connected_queue():
    await message_queue.connect()
    yield message_queue
    await message_queue.disconnect()


@pytest.mark.asyncio
async def test_orchestrator_creates_plan(connected_queue):
    """Orchestrator must return a plan with 3 steps"""
    agent = OrchestratorAgent()
    msg = Message(
        task_id="test-001",
        agent_name="orchestrator",
        payload={"task": "Research AI trends and write a summary"},
        step="start"
    )
    result = await agent.process(msg)
    assert result is not None
    assert result.agent_name == "retriever"
    assert "plan" in result.payload
    assert len(result.payload["plan"]) == 3
    print("test_orchestrator_creates_plan PASSED")


@pytest.mark.asyncio
async def test_retriever_returns_data(connected_queue):
    """Retriever must return retrieved information"""
    agent = RetrieverAgent()
    msg = Message(
        task_id="test-002",
        agent_name="retriever",
        payload={
            "task": "Research AI trends",
            "plan": [
                {"step_number": 1, "title": "Research", "description": "Find AI trends", "agent": "retriever"},
                {"step_number": 2, "title": "Analyze",  "description": "Analyze data",   "agent": "analyzer"},
                {"step_number": 3, "title": "Write",    "description": "Write report",   "agent": "writer"},
            ],
            "current_step": 0,
            "results": {}
        },
        step="retrieval"
    )
    result = await agent.process(msg)
    assert result is not None
    assert result.agent_name == "analyzer"
    assert "retrieval" in result.payload["results"]
    assert len(result.payload["results"]["retrieval"]) > 100
    print("test_retriever_returns_data PASSED")


@pytest.mark.asyncio
async def test_analyzer_extracts_insights(connected_queue):
    """Analyzer must return analysis from retrieved data"""
    agent = AnalyzerAgent()
    msg = Message(
        task_id="test-003",
        agent_name="analyzer",
        payload={
            "task": "Research AI trends",
            "plan": [
                {"step_number": 2, "title": "Analyze", "description": "Analyze trends", "agent": "analyzer"},
            ],
            "current_step": 1,
            "results": {
                "retrieval": "AI is growing rapidly. Machine learning, deep learning, and LLMs are key trends. ChatGPT reached 100M users in 2 months. AI investment hit $91B in 2022."
            }
        },
        step="analysis"
    )
    result = await agent.process(msg)
    assert result is not None
    assert result.agent_name == "writer"
    assert "analysis" in result.payload["results"]
    assert len(result.payload["results"]["analysis"]) > 100
    print("test_analyzer_extracts_insights PASSED")


@pytest.mark.asyncio
async def test_writer_produces_report(connected_queue):
    """Writer must produce a final report"""
    agent = WriterAgent()
    msg = Message(
        task_id="test-004",
        agent_name="writer",
        payload={
            "task": "Research AI trends and write a report",
            "plan": [
                {"step_number": 3, "title": "Write", "description": "Write report", "agent": "writer"},
            ],
            "current_step": 2,
            "results": {
                "retrieval": "AI is growing rapidly with major advances in LLMs.",
                "analysis":  "Key insight: LLMs are transforming every industry."
            }
        },
        step="writing"
    )
    result = await agent.process(msg)
    assert result is not None
    assert result.payload["status"] == "completed"
    assert "final_report" in result.payload
    assert len(result.payload["final_report"]) > 200
    print("test_writer_produces_report PASSED")