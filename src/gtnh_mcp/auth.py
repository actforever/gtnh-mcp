"""Fixed API-key authentication and opaque restore ownership metadata."""

import base64
import binascii
import hmac
import re
from dataclasses import dataclass

from .config import Settings

ACTOR_HEADER = "X-GTNH-Actor"


class Denied(ValueError):
    pass


@dataclass(frozen=True)
class Actor:
    # Kept as the original string in journals for pre-migration compatibility.
    key: str = "api-client"


def encode_actor(actor: Actor) -> str:
    return (
        base64.urlsafe_b64encode(actor.key.encode("utf-8")).decode("ascii").rstrip("=")
    )


def decode_actor(value: str | None) -> Actor:
    if value is None:
        return Actor()
    try:
        if len(value) > 1368 or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
            raise ValueError
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        key = raw.decode("utf-8")
        if not key or len(raw) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in key):
            raise ValueError
        actor = Actor(key)
        if encode_actor(actor) != value.rstrip("="):
            raise ValueError
        return actor
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise Denied("调用者标识格式无效") from exc


def verify(token: str, settings: Settings, actor_header: str | None = None) -> Actor:
    if not hmac.compare_digest(
        token.encode("utf-8"), settings.auth_secret.get_secret_value().encode("utf-8")
    ):
        raise Denied("访问密钥无效")
    return decode_actor(actor_header)
