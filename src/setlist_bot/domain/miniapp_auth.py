"""RU: Только проверенные Telegram initData. EN: Never trust initDataUnsafe or client IDs."""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from setlist_bot.domain.rules import require


def verify_init_data(raw: str, key: bytes, at: int | None = None) -> int:
    require(0 < len(raw) <= 16384, "miniapp_auth")
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        data = dict(pairs)
        require(len(data) == len(pairs), "miniapp_auth")
        signature = data.pop("hash")
        check = "\n".join(f"{name}={value}" for name, value in sorted(data.items()))
        expected = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
        require(hmac.compare_digest(signature, expected), "miniapp_auth")
        current = int(time.time()) if at is None else at
        require(-30 <= current - int(data["auth_date"]) <= 3600, "miniapp_auth")
        require(data.get("chat_type", "") in ("", "private", "sender"), "private_only")
        actor = json.loads(data["user"])
        identifier = actor["id"]
        require(
            type(identifier) is int and 0 < identifier < 2**63 and not actor.get("is_bot"),
            "miniapp_auth",
        )
        return int(identifier)
    except (KeyError, ValueError, TypeError, UnicodeError):
        from setlist_bot.domain.rules import AppError

        raise AppError("miniapp_auth") from None
