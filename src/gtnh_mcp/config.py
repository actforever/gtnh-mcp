"""Environment-only deployment configuration; no secrets in reprs."""

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class Settings(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    rcon_host: str = "127.0.0.1"
    rcon_port: int = Field(default=25575, ge=1, le=65535)
    rcon_password: SecretStr
    rcon_timeout: int = Field(default=10, ge=1, le=120)
    auth_secret: SecretStr
    allowed_groups: list[str]
    admin_users: list[str] = []
    mcp_host: str = "127.0.0.1"
    mcp_port: int = Field(default=8000, ge=1, le=65535)
    runtime_dir: Path = Path("/run/gtnh")
    server_root: Path = Path("/server")
    backup_dir: Path = Path("/backups")
    state_dir: Path = Path("/state")
    container_name: str = "gtnh"
    stop_timeout: int = Field(default=180, ge=1)
    startup_timeout: int = Field(default=900, ge=1)
    max_archive_bytes: int = Field(default=100 * 1024**3, ge=1)
    max_archive_members: int = Field(default=1000000, ge=1)
    free_space_reserve: int = Field(default=1024**3, ge=0)

    @model_validator(mode="after")
    def validate_secrets(self):
        if len(self.auth_secret.get_secret_value()) < 32:
            raise ValueError("AUTH_SECRET must contain at least 32 characters")
        if not self.rcon_password.get_secret_value():
            raise ValueError("RCON_PASSWORD must not be empty")
        if not self.allowed_groups:
            raise ValueError("ALLOWED_GROUPS must not be empty")
        return self

    @classmethod
    def from_env(cls):
        values = {}
        for name in cls.model_fields:
            value = os.environ.get(name.upper())
            if value is not None:
                values[name] = (
                    json.loads(value)
                    if name in {"allowed_groups", "admin_users"}
                    else value
                )
        return cls(**values)

    @property
    def socket_path(self) -> Path:
        return self.runtime_dir / "restore.sock"

    @property
    def lock_path(self) -> Path:
        return self.runtime_dir / "operations.lock"
