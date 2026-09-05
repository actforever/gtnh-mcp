"""Short-lived audience-bound assertions from the trusted AstrBot bridge."""

import time
from dataclasses import dataclass

import jwt

from .config import Settings


class Denied(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    platform: str
    group: str
    user: str
    purpose: str = "tool"
    confirmation: str = ""

    @property
    def key(self) -> str:
        return f"{self.platform}:{self.group}:{self.user}"

    def is_admin(self, settings: Settings) -> bool:
        return f"{self.platform}:{self.user}" in settings.admin_users

    def require_admin(self, settings: Settings) -> None:
        if not self.is_admin(settings):
            raise Denied("此操作仅限配置中的管理员")


def verify(token: str, settings: Settings) -> Actor:
    try:
        claims = jwt.decode(
            token,
            settings.auth_secret.get_secret_value(),
            algorithms=["HS256"],
            audience="gtnh-mcp",
            issuer="astrbot-gtnh",
            options={
                "require": [
                    "exp",
                    "iat",
                    "iss",
                    "aud",
                    "sub",
                    "platform",
                    "group",
                    "purpose",
                ]
            },
        )
        if claims["exp"] - claims["iat"] > 60 or claims["iat"] > time.time():
            raise ValueError("Invalid lifetime")
        for key in ("platform", "group", "sub"):
            if (
                not isinstance(claims[key], str)
                or not claims[key]
                or ":" in claims[key]
                or any(ord(c) < 32 for c in claims[key])
            ):
                raise ValueError("Invalid identity")
        if claims["purpose"] not in {"tool", "confirm"}:
            raise ValueError("Invalid purpose")
        actor = Actor(
            claims["platform"],
            claims["group"],
            claims["sub"],
            claims["purpose"],
            claims.get("confirmation", ""),
        )
        if f"{actor.platform}:{actor.group}" not in settings.allowed_groups:
            raise ValueError("Group denied")
        return actor
    except (jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        raise Denied("身份凭据无效、过期或群未授权") from exc
