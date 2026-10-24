from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCACHE_")

    embed_model: str = "BAAI/bge-small-en-v1.5"
    llm_tag: str = "qwen2.5:7b"  # 16 GB tier. 8 GB tier: qwen2.5:3b (DECISION_LOG 2026-10-24)
    prompt_version: str = "v4"  # key of scache.llm.PROMPTS; stored with any answer that is cached
    ollama_url: str = "http://localhost:11434"
    temperature: float = 0
    num_ctx: int = 8192
    timeout_s: float = 300.0
    # Similarity thresholds (HIGH: `cli sweep` with the entity guard, M4; LOW: `cli verifiers` with the 7B judge, M5;
    # both 2026-10-24). Change only with a DECISION_LOG entry.
    high: float = 0.93
    low: float = 0.80
    verify_high: bool = True  # M5: the judge also checks candidates >= HIGH (catches same-entity, different-intent)
    ttl_s: float = 24 * 3600  # backstop for a policy change nobody bumped the version for; a judgment call (DECISION_LOG M6)
    # /admin/invalidate default: the min_sim the M6 rule chose on Set C (covers 11 of 20 stale entries, deletes 0 controls)
    invalidate_min_sim: float = 0.85
    admin_token: str | None = None  # if set, /admin/invalidate needs header X-Admin-Token with this value
    policy_version: str = "v1"
    tenant: str = "demo"
    policy_path: Path = Path(__file__).resolve().parents[2] / "data" / "policy.md"
    cache_dir: Path = Path("cache")
    log_db: Path = Path("logs/requests.sqlite")


settings = Settings()
