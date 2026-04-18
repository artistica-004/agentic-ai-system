# System Design Document
## Agentic AI System for Multi-Step Tasks

---

## 1. Architecture Overview

```
User → FastAPI → Pipeline → Orchestrator → Retriever → Analyzer → Writer → User
                    ↕            ↕             ↕           ↕          ↕
                  Redis        Redis         Redis       Redis      Redis
                  Queue        Queue         Queue       Queue     Pub/Sub
```

The system uses a message-passing architecture where agents are fully
decoupled — they never call each other directly. All communication
happens through Redis queues.

---

## 2. Agent Responsibilities

| Agent | Queue | Input | Output |
|---|---|---|---|
| Orchestrator | queue:orchestrator | Raw user task | Structured 3-step plan |
| Retriever | queue:retriever | Task + plan | Retrieved information |
| Analyzer | queue:analyzer | Retrieved data | Key insights |
| Writer | queue:writer | Insights + data | Final report |

---

## 3. Message Flow

1. User sends POST /task with their task text
2. FastAPI creates a task_id and pushes Message to queue:orchestrator
3. Orchestrator reads message, calls Groq LLM to create plan
4. Orchestrator pushes result to queue:retriever
5. Retriever reads message, calls Groq LLM to gather information
6. Retriever pushes result to queue:analyzer
7. Analyzer reads message, calls Groq LLM to extract insights
8. Analyzer pushes result to queue:writer
9. Writer reads message, calls Groq LLM to write final report
10. Writer stores result in Redis, publishes to stream:{task_id}
11. User receives final report via SSE stream

---

## 4. Async Pipeline Design

- Each agent runs as an independent asyncio.Task
- Agents use BRPOP (blocking pop) — efficient waiting with no CPU waste
- asyncio.wait_for() enforces per-agent timeouts
- No agent blocks another — fully concurrent

---

## 5. Failure Handling

| Failure Type | Handling Strategy |
|---|---|
| LLM API timeout | Retry with exponential backoff (1s, 2s, 4s) |
| LLM bad JSON | 3 parsing strategies + safe fallback default |
| Agent crash | safe_process() catches all exceptions |
| All retries fail | Message moved to Dead Letter Queue |
| Redis disconnect | Connection pooling + reconnect on next call |

---

## 6. Scalability Considerations

- **Horizontal scaling**: Multiple instances of any agent can read from the same queue
- **Queue backpressure**: Redis lists naturally buffer work during traffic spikes
- **Manual batching**: pop_batch() processes up to 5 messages per cycle
- **Result TTL**: Results expire after 1 hour — prevents Redis memory growth
- **Stateless agents**: No shared state between agents — easy to scale out