"""Environment-only deployment configuration; no secrets in reprs."""

import os
import re
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


def validate_world_directory(value: str) -> str:
    if (
        not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}", value)
        or value.lower()
        in {
            "visualprospecting",
            "backup",
            "backups",
            "con",
            "prn",
            "aux",
            "nul",
            *{f"com{i}" for i in range(1, 10)},
            *{f"lpt{i}" for i in range(1, 10)},
        }
        or value.endswith(".")
    ):
        raise ValueError(
            "WORLD_DIRECTORY must be a safe, distinct single directory name"
        )
    return value


class Settings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    rcon_host: str = "127.0.0.1"
    rcon_port: int = Field(default=25575, ge=1, le=65535)
    rcon_password: SecretStr
    rcon_timeout: int = Field(default=10, ge=1, le=120)
    auth_secret: SecretStr
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8000, ge=1, le=65535)
    runtime_dir: Path = Path("/run/gtnh")
    server_root: Path = Path("/gtnh")
    backup_dir: Path = Path("/gtnh/backups")
    state_dir: Path = Path("/state")
    container_name: str = "gtnh"
    world_directory: str = "World"
    stop_timeout: int = Field(default=180, ge=1)
    startup_timeout: int = Field(default=900, ge=1)
    max_archive_bytes: int = Field(default=100 * 1024**3, ge=1)
    max_archive_members: int = Field(default=1000000, ge=1)
    free_space_reserve: int = Field(default=1024**3, ge=0)

    @field_validator("world_directory")
    @classmethod
    def check_world_directory(cls, value):
        return validate_world_directory(value)

    @property
    def world_dirs(self) -> tuple[str, str]:
        return self.world_directory, "visualprospecting"

    @model_validator(mode="after")
    def validate_secrets(self):
        if len(self.auth_secret.get_secret_value()) < 32:
            raise ValueError("AUTH_SECRET must contain at least 32 characters")
        if not self.rcon_password.get_secret_value():
            raise ValueError("RCON_PASSWORD must not be empty")
        return self

    @classmethod
    def from_env(cls):
        values = {}
        for name in cls.model_fields:
            value = os.environ.get(name.upper())
            if value is not None:
                values[name] = value
        return cls(**values)

    @property
    def socket_path(self) -> Path:
        return self.runtime_dir / "restore.sock"

    @property
    def lock_path(self) -> Path:
        return self.runtime_dir / "operations.lock"
