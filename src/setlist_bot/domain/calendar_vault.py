"""RU: Аутентифицированное шифрование ссылок. EN: Key stays outside the database."""

import hmac
import json
import re
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken

from setlist_bot.domain.rules import AppError


class CalendarVault:
    def __init__(self, key: str) -> None:
        self.cipher = Fernet(key.encode("ascii"))

    def seal(self, identifier: UUID, token: str) -> str:
        payload = json.dumps({"id": str(identifier), "token": token}).encode()
        return self.cipher.encrypt(payload).decode("ascii")

    def open(self, identifier: UUID, ciphertext: str, digest: str) -> str:
        from hashlib import sha256

        try:
            payload = json.loads(self.cipher.decrypt(ciphertext.encode("ascii")))
            token = payload["token"]
            if (
                payload["id"] != str(identifier)
                or not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
                or not hmac.compare_digest(sha256(token.encode("ascii")).hexdigest(), digest)
            ):
                raise ValueError()
            return token
        except (InvalidToken, ValueError, KeyError, TypeError, UnicodeError):
            # RU/EN: Never echo the key, ciphertext, token or underlying crypto error.
            raise AppError("calendar_link_unreadable") from None
