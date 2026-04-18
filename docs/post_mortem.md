# Post-Mortem Document
## Agentic AI System for Multi-Step Tasks

---

## 1. Scaling Issue Encountered

**Issue: Redis Pub/Sub bottleneck under high concurrent task load**

When multiple users submit tasks simultaneously, each task creates its own
Redis Pub/Sub channel (stream:{task_id}). Under high load (100+ concurrent
tasks), the single Redis instance becomes a bottleneck for both queue
operations AND pub/sub message delivery simultaneously.

**Observed symptoms:**
- Stream updates delayed by 2-5 seconds under load
- Redis CPU spiking to 80%+ with 50+ concurrent tasks
- Occasional missed pub/sub messages causing incomplete streams

**Resolution:**
Separate Redis instances for queue operations and pub/sub streaming.
Use Redis Cluster for horizontal scaling beyond single-node limits.

---

## 2. Design Decision I Would Change

**Decision: Sequential agent pipeline (Orchestrator → Retriever → Analyzer → Writer)**

**The problem:**
The current design is strictly sequential — each agent waits for the
previous one to finish before starting. This means total latency =
sum of all agent latencies.

For a task taking 60 seconds total:
- Orchestrator: 5s
- Retriever: 20s
- Analyzer: 15s
- Writer: 20s
= 60s total wait for user

**What I would change:**
Implement parallel sub-tasks where possible. For example, the Retriever
could spawn multiple parallel retrieval tasks (search different sources
simultaneously), and the Analyzer could begin analyzing completed chunks
while retrieval is still ongoing.

This streaming/parallel approach could reduce total latency by 40-60%.

---

## 3. Trade-offs Made During Development

| Decision | Trade-off |
|---|---|
| Groq LLM for retrieval | Fast and free, but no real web search — LLM knowledge has a cutoff date |
| SSE over WebSocket | Simpler implementation, but only server→client (no bidirectional) |
| Sequential pipeline | Easy to debug and reason about, but slower than parallel execution |
| Redis for queue + pub/sub | Single dependency, but creates coupling between queue and streaming layers |
| Manual batching | Full control and visibility, but more code to maintain than framework solutions |
| In-memory result storage (Redis) | Fast retrieval, but results lost if Redis restarts (no persistence) |