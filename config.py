# config.py
# This file manages ALL settings and environment variables for the entire system.
# Instead of hardcoding values like API keys or ports everywhere,
# we load them once here and import them wherever needed.

from pydantic_settings import BaseSettings
from pydantic import Field
from dotenv import load_dotenv

# Load the .env file so environment variables are available
load_dotenv()


class Settings(BaseSettings):
    """
    Settings class reads values from your .env file automatically.
    Each variable here maps directly to a line in your .env file.
    If a .env value is missing, it uses the default value shown below.
    """

    # --- Groq AI API ---
    groq_api_key: str = Field(..., env="GROQ_API_KEY")
    # The '...' means this field is REQUIRED — app will crash if missing
    # This is intentional — you should never run without an API key

    groq_model: str = Field(
        default="llama-3.3-70b-versatile", env="GROQ_MODEL"
    )
    # llama-3.3-70b-versatile is the latest versatile model on Groq
    # It provides excellent performance across various tasks

    # --- Redis Message Queue ---
    redis_host: str = Field(default="localhost", env="REDIS_HOST")
    redis_port: int = Field(default=6379, env="REDIS_PORT")
    redis_db: int = Field(default=0, env="REDIS_DB")
    # Redis uses databases numbered 0-15
    # We use DB 0 (the default)

    # --- FastAPI Server ---
    app_host: str = Field(default="0.0.0.0", env="APP_HOST")
    app_port: int = Field(default=8000, env="APP_PORT")
    # 0.0.0.0 means "accept connections from any IP"
    # Port 8000 is the standard FastAPI port

    # --- Retry & Failure Handling ---
    max_retries: int = Field(default=3, env="MAX_RETRIES")
    retry_base_delay: float = Field(default=1.0, env="RETRY_BASE_DELAY")
    agent_timeout: float = Field(default=60.0, env="AGENT_TIMEOUT")
    # max_retries: how many times to retry a failed agent call
    # retry_base_delay: starting wait time before retry (in seconds)
    #   With exponential backoff: 1s, 2s, 4s between retries
    # agent_timeout: max seconds to wait for any single agent

    # --- Queue Names (Redis keys) ---
    # Each agent has its own queue in Redis
    # Think of these as named "mailboxes" for each agent
    orchestrator_queue: str = "queue:orchestrator"
    retriever_queue: str = "queue:retriever"
    analyzer_queue: str = "queue:analyzer"
    writer_queue: str = "queue:writer"
    results_queue: str = "queue:results"
    dead_letter_queue: str = "queue:dead_letter"
    # dead_letter_queue: where messages go when they fail after all retries
    # This lets us inspect what failed without losing the data

    # --- Batch Settings ---
    batch_size: int = Field(default=5, env="BATCH_SIZE")
    batch_timeout: float = Field(default=2.0, env="BATCH_TIMEOUT")
    # batch_size: how many messages to process together at once
    # batch_timeout: how long to wait to fill a batch before processing anyway
    # Manual batching = we control this logic ourselves (no framework does it)

    class Config:
        # Tell pydantic-settings to read from .env file
        env_file = ".env"
        # Allow extra fields without crashing
        extra = "ignore"


# Create a single shared instance of Settings
# Every other file will import THIS object — not the class
# This way settings are loaded only once when the app starts
settings = Settings()


# --- Redis Connection URL ---
# Build the Redis URL from individual settings
# Format: redis://hostname:port/database_number
REDIS_URL = (
    f"redis://{settings.redis_host}:{settings.redis_port}/{settings.redis_db}"
)

# --- Queue Name Constants ---
# Export queue names as easy-to-use constants
# Other files import these instead of typing strings manually
ORCHESTRATOR_QUEUE = settings.orchestrator_queue
RETRIEVER_QUEUE = settings.retriever_queue
ANALYZER_QUEUE = settings.analyzer_queue
WRITER_QUEUE = settings.writer_queue
RESULTS_QUEUE = settings.results_queue
DEAD_LETTER_QUEUE = settings.dead_letter_queue