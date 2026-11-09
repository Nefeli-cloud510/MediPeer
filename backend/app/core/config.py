from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEDIPEEPER_",
        env_file=(".env", ".env.local"),
        extra="ignore",
    )

    app_name: str = "MediPeer"
    app_env: str = "development"
    project_root: Path = Path(__file__).resolve().parents[3]
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ]
    )

    database_url: str = "sqlite:///./data/medipeer.db"
    auth_secret: str = "dev-only-change-me"
    token_ttl_seconds: int = 12 * 3600

    llm_provider: str = Field(
        default="stub",
        validation_alias=AliasChoices("MEDIPEEPER_LLM_PROVIDER", "MEDIPEER_LLM_PROVIDER"),
    )
    llm_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MEDIPEEPER_LLM_BASE_URL", "MEDIPEER_LLM_BASE_URL"),
    )
    llm_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("MEDIPEEPER_LLM_API_KEY", "MEDIPEER_LLM_API_KEY"),
    )
    llm_model: str = Field(
        default="medipeer-stub",
        validation_alias=AliasChoices("MEDIPEEPER_LLM_MODEL", "MEDIPEER_LLM_MODEL"),
    )
    # DashScope / 阿里云百炼兼容配置。密钥只从后端 .env 读取。
    dashscope_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DASHSCOPE_API_KEY",
            "MEDIPEEPER_DASHSCOPE_API_KEY",
            "BAILIAN_API_KEY",
        ),
    )
    dashscope_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "DASHSCOPE_BASE_URL",
            "MEDIPEEPER_DASHSCOPE_BASE_URL",
            "BAILIAN_BASE_URL",
        ),
    )
    dashscope_model: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BAILIAN_MODEL",
            "DASHSCOPE_MODEL",
            "MEDIPEEPER_BAILIAN_MODEL",
        ),
    )
    dashscope_workspace_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "BAILIAN_WORKSPACE_ID",
            "DASHSCOPE_WORKSPACE_ID",
            "MEDIPEEPER_BAILIAN_WORKSPACE_ID",
        ),
    )
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    max_input_chars: int = 40_000
    max_concurrent_llm: int = 2

    rules_version: str = "2026.11.05.1"
    prompt_version_patient_facts: str = "v1"
    prompt_version_prior_claims: str = "v1"
    prompt_version_claim_relations: str = "v1"
    prompt_version_draft_review: str = "v1"
    prompt_version_direct_baseline: str = "v1"

    demo_password: str = "demo1234"
    judge_password: str = "judge1234"

    @property
    def resolved_llm_provider(self) -> str:
        if self.llm_provider != "stub":
            return self.llm_provider
        if self.dashscope_api_key:
            return "openai_compatible"
        return "stub"

    @property
    def resolved_llm_base_url(self) -> str | None:
        if self.llm_base_url:
            return self.llm_base_url
        if self.dashscope_base_url:
            return self.dashscope_base_url.rstrip("/")
        if self.resolved_llm_provider != "stub":
            return "https://dashscope.aliyuncs.com/compatible-mode/v1".rstrip("/")
        return None

    @property
    def resolved_llm_api_key(self) -> str | None:
        if self.llm_api_key:
            return self.llm_api_key
        return self.dashscope_api_key

    @property
    def resolved_llm_model(self) -> str:
        if self.llm_model and self.llm_model != "medipeer-stub":
            return self.llm_model
        if self.dashscope_model:
            return self.dashscope_model
        return self.llm_model


@lru_cache
def get_settings() -> Settings:
    return Settings()

