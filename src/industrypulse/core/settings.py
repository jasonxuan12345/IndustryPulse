"""Local settings with secret-safe representations and environment diagnostics."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data_dir: Path = Path("data")
    artifact_dir: Path = Path("artifacts")
    api_key: SecretStr | None = Field(default=None, exclude=True)
    discovery_enabled: bool = False
    max_attempts: int = Field(default=3, ge=1, le=10)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if env is None else env
        enabled = values.get("INDUSTRYPULSE_DISCOVERY_ENABLED", "false").lower()
        if enabled not in {"true", "false"}:
            raise ValueError("INDUSTRYPULSE_DISCOVERY_ENABLED must be true/false")
        key = values.get("INDUSTRYPULSE_API_KEY")
        return cls(
            data_dir=Path(values.get("INDUSTRYPULSE_DATA_DIR", "data")),
            artifact_dir=Path(values.get("INDUSTRYPULSE_ARTIFACT_DIR", "artifacts")),
            api_key=SecretStr(key) if key else None,
            discovery_enabled=enabled == "true",
            max_attempts=int(values.get("INDUSTRYPULSE_MAX_ATTEMPTS", "3")),
        )

    def diagnostics(self) -> dict[str, str | bool]:
        return {
            "python": sys.version.split()[0],
            "python_supported": (3, 12) <= sys.version_info[:2] < (3, 14),
            "data_dir": str(self.data_dir.resolve()),
            "artifact_dir": str(self.artifact_dir.resolve()),
            "research_provider": "configured" if self.api_key else "manual_file",
            "discovery_enabled": self.discovery_enabled,
        }
