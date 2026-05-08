"""Application configuration loaded from environment variables.

Two Databento API keys are supported so that OPRA (US options) and GLBX (CME
futures) entitlements can be billed/managed independently. A single legacy key
is also accepted as a fallback.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings.

    Environment variables (all optional except at least one Databento key):
      - DATABENTO_API_KEY_OPRA: key with OPRA.PILLAR access (preferred)
      - DATABENTO_API_KEY_GLBX: key with GLBX.MDP3 access (preferred)
      - DATABENTO_API_KEY: legacy single key, used as fallback for both
      - GEX_RISK_FREE_RATE: annualized continuously compounded rate (default 0.045)
      - GEX_DIVIDEND_YIELD: annualized continuous dividend yield for SPX (default 0.013)
      - GEX_DEFAULT_EXPIRY_HORIZON_DAYS: max DTE returned for /api/expiries (default 14)
      - CORS_ORIGINS: comma-separated list (default "*")
    """

    databento_api_key_opra: str | None = Field(default=None, alias="DATABENTO_API_KEY_OPRA")
    databento_api_key_glbx: str | None = Field(default=None, alias="DATABENTO_API_KEY_GLBX")
    databento_api_key: str | None = Field(default=None, alias="DATABENTO_API_KEY")

    gex_risk_free_rate: float = Field(default=0.045, alias="GEX_RISK_FREE_RATE")
    gex_dividend_yield: float = Field(default=0.013, alias="GEX_DIVIDEND_YIELD")
    gex_default_expiry_horizon_days: int = Field(
        default=14, alias="GEX_DEFAULT_EXPIRY_HORIZON_DAYS"
    )
    cors_origins: str = Field(default="*", alias="CORS_ORIGINS")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def opra_key(self) -> str | None:
        """Resolve effective OPRA key (specific > legacy)."""
        return self.databento_api_key_opra or self.databento_api_key

    @property
    def glbx_key(self) -> str | None:
        """Resolve effective GLBX key (specific > legacy)."""
        return self.databento_api_key_glbx or self.databento_api_key

    @property
    def cors_origins_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()
