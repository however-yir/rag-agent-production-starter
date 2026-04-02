"""Environment-backed application settings."""

from __future__ import annotations

from dataclasses import dataclass, field
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during barebones execution
    load_dotenv = None


def _load_dotenv() -> None:
    if load_dotenv is not None:
        load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return int(raw_value)


def _get_float(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return float(raw_value)


@dataclass(slots=True)
class AppSettings:
    openai_api_key: str = ""
    tavily_api_key: str = ""
    pinecone_api_key: str = ""
    openai_model: str = "gpt-4.1"
    embedding_model: str = "text-embedding-3-large"
    pinecone_index_name: str = "ragagent1"
    tavily_max_results: int = 3
    retrieval_top_k: int = 4
    embedding_dimension: int = 64
    chunk_size: int = 700
    chunk_overlap: int = 80
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True
    database_path: str = "data/rag_agent.db"
    ingestion_max_retries: int = 3
    ingestion_retry_backoff_seconds: int = 2
    ingestion_retry_max_backoff_seconds: int = 120
    ingestion_worker_poll_seconds: float = 1.0
    ingestion_worker_batch_size: int = 5
    ingestion_queue_backend: str = "sqlite"
    ingestion_embedded_worker_enabled: bool = True
    redis_url: str = ""
    security_enabled: bool = True
    jwt_secret: str = "change-me-in-production"
    jwt_issuer: str = "rag-react-agent"
    jwt_access_token_exp_minutes: int = 60
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin123"
    rate_limit_per_minute: int = 120
    prometheus_enabled: bool = True
    open_telemetry_enabled: bool = False
    open_telemetry_logs_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    otel_exporter_otlp_logs_endpoint: str = ""
    otel_service_name: str = "rag-react-agent"
    otel_service_environment: str = "dev"
    use_mock_services: bool = True
    enable_langgraph_agent: bool = True
    enable_langgraph_rag: bool = True
    system_prompt: str = "You are a helpful assistant."
    policy_system_prompt: str = (
        "You are a helpful assistant answering questions from official customer "
        "service policies."
    )
    default_queries: tuple[str, ...] = field(
        default_factory=lambda: (
            "How should staff respond if a service animal becomes unruly or disruptive?",
            "What is the weather in Kanyakumari today?",
        )
    )

    @classmethod
    def from_env(cls) -> "AppSettings":
        _load_dotenv()
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            pinecone_api_key=os.getenv("PINECONE_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-large"),
            pinecone_index_name=os.getenv("PINECONE_INDEX_NAME", "ragagent1"),
            tavily_max_results=_get_int("TAVILY_MAX_RESULTS", 3),
            retrieval_top_k=_get_int("RETRIEVAL_TOP_K", 4),
            embedding_dimension=_get_int("EMBEDDING_DIMENSION", 64),
            chunk_size=_get_int("CHUNK_SIZE", 700),
            chunk_overlap=_get_int("CHUNK_OVERLAP", 80),
            app_host=os.getenv("APP_HOST", "0.0.0.0"),
            app_port=_get_int("APP_PORT", 8000),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_json=_get_bool("LOG_JSON", True),
            database_path=os.getenv("DATABASE_PATH", "data/rag_agent.db"),
            ingestion_max_retries=_get_int("INGESTION_MAX_RETRIES", 3),
            ingestion_retry_backoff_seconds=_get_int("INGESTION_RETRY_BACKOFF_SECONDS", 2),
            ingestion_retry_max_backoff_seconds=_get_int(
                "INGESTION_RETRY_MAX_BACKOFF_SECONDS",
                120,
            ),
            ingestion_worker_poll_seconds=_get_float("INGESTION_WORKER_POLL_SECONDS", 1.0),
            ingestion_worker_batch_size=_get_int("INGESTION_WORKER_BATCH_SIZE", 5),
            ingestion_queue_backend=os.getenv("INGESTION_QUEUE_BACKEND", "sqlite"),
            ingestion_embedded_worker_enabled=_get_bool(
                "INGESTION_EMBEDDED_WORKER_ENABLED",
                True,
            ),
            redis_url=os.getenv("REDIS_URL", ""),
            security_enabled=_get_bool("SECURITY_ENABLED", True),
            jwt_secret=os.getenv("JWT_SECRET", "change-me-in-production"),
            jwt_issuer=os.getenv("JWT_ISSUER", "rag-react-agent"),
            jwt_access_token_exp_minutes=_get_int("JWT_ACCESS_TOKEN_EXP_MINUTES", 60),
            bootstrap_admin_username=os.getenv("BOOTSTRAP_ADMIN_USERNAME", "admin"),
            bootstrap_admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "admin123"),
            rate_limit_per_minute=_get_int("RATE_LIMIT_PER_MINUTE", 120),
            prometheus_enabled=_get_bool("PROMETHEUS_ENABLED", True),
            open_telemetry_enabled=_get_bool("OPEN_TELEMETRY_ENABLED", False),
            open_telemetry_logs_enabled=_get_bool("OPEN_TELEMETRY_LOGS_ENABLED", False),
            otel_exporter_otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""),
            otel_exporter_otlp_logs_endpoint=os.getenv(
                "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
                "",
            ),
            otel_service_name=os.getenv("OTEL_SERVICE_NAME", "rag-react-agent"),
            otel_service_environment=os.getenv("OTEL_SERVICE_ENV", "dev"),
            use_mock_services=_get_bool("USE_MOCK_SERVICES", True),
            enable_langgraph_agent=_get_bool("ENABLE_LANGGRAPH_AGENT", True),
            enable_langgraph_rag=_get_bool("ENABLE_LANGGRAPH_RAG", True),
        )

    @property
    def live_llm_ready(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def live_search_ready(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def live_vector_store_ready(self) -> bool:
        return bool(self.pinecone_api_key and self.openai_api_key)
