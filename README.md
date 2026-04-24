# AgentRelay — agents passing work down the relay

A production-grade multi-agent AI system built with FastAPI, Redis, and Groq LLM.

## Architecture

```
User → FastAPI → Orchestrator → Retriever → Analyzer → Writer → SSE Stream → User
                      ↕              ↕           ↕         ↕
                   Redis Queue   Redis Queue  Redis Queue  Redis Queue
```

## Agents

| Agent | Role |
|---|---|
| Orchestrator | Breaks task into steps |
| Retriever | Gathers information |
| Analyzer | Extracts insights |
| Writer | Composes final report |

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/yourusername/agentic-ai-system.git
cd agentic-ai-system
```

### 2. Create virtual environment
```bash
python -m venv venv
venv\Scripts\activate   # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Start Redis
```bash
docker run -d --name redis-agent -p 6379:6379 redis:7.2
```

### 5. Configure environment
```bash
cp .env.example .env
# Edit .env and add your GROQ_API_KEY
```

### 6. Run the server
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| POST | /task | Submit a new task |
| GET | /stream/{task_id} | Stream live progress (SSE) |
| GET | /result/{task_id} | Get final result |
| GET | /health | System health check |
| GET | /dead-letter | View failed messages |
| DELETE | /queues | Clear all queues |

## Example Usage

```bash
# Submit a task
curl -X POST http://localhost:8000/task \
  -H "Content-Type: application/json" \
  -d '{"task": "Research climate change and write a report"}'

# Stream progress (returns task_id from above)
curl http://localhost:8000/stream/{task_id}

# Get final result
curl http://localhost:8000/result/{task_id}
```

## Running Tests

```bash
pytest tests/ -v
```

## Tech Stack

- **FastAPI** — async web framework
- **Redis** — message queue + pub/sub streaming
- **Groq** — LLM API (llama-3.3-70b-versatile)
- **asyncio** — async/await throughout
- **pydantic** — data validation
- **pytest** — testing

  <img width="1107" height="862" alt="image" src="https://github.com/user-attachments/assets/2aa14c19-66d3-4859-8e72-b5f0a91b4c56" />


## 📄 Documentation
Full technical documentation: [View Docs](docs/agentic_ai_documentation.docx)
