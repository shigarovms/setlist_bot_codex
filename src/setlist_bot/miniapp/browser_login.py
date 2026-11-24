"""Telegram OIDC and revocable browser sessions for existing bot users."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import timedelta
from urllib.parse import urlencode
from uuid import UUID

import aiohttp
import jwt
from aiohttp import web
from cryptography.fernet import Fernet, InvalidToken

from setlist_bot.application.ports import Store
from setlist_bot.domain.entities import Confirmation, User, now
from setlist_bot.domain.rules import AppError, require
from setlist_bot.miniapp.settings import MiniAppSettings

ISSUER = "https://oauth.telegram.org"
CALLBACK_PATH = "/miniapp/auth/callback"
FLOW_COOKIE = "__Secure-setlist_login"
SESSION_COOKIE = "__Secure-setlist_session"
FLOW_TTL = 600
SESSION_TTL = 12 * 3600


class BrowserLogin:
    def __init__(self, settings: MiniAppSettings) -> None:
        secret = settings.browser_login_secret
        require(secret is not None, "miniapp_auth")
        assert secret is not None
        self.secret = secret.get_secret_value()
        self.client_id = str(settings.expected_bot_id)
        self.redirect = settings.public_base_url + CALLBACK_PATH
        key = hmac.digest(
            base64.urlsafe_b64decode(settings.auth_key.get_secret_value()),
            b"setlist/browser-login/" + self.redirect.encode(),
            "sha256",
        )
        self.flow = Fernet(base64.urlsafe_b64encode(key))

    def begin(self, invitation_id: UUID | None = None) -> web.Response:
        flow = {key: secrets.token_urlsafe(32) for key in ("state", "nonce", "verifier")}
        if invitation_id is not None:
            flow["invitation"] = str(invitation_id)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest())
        authorize = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect,
                "response_type": "code",
                "scope": "openid profile",
                "state": flow["state"],
                "nonce": flow["nonce"],
                "code_challenge": challenge.decode().rstrip("="),
                "code_challenge_method": "S256",
            }
        )
        response = web.Response(status=303, headers={"Location": f"{ISSUER}/auth?{authorize}"})
        response.set_cookie(
            FLOW_COOKIE,
            self.flow.encrypt(json.dumps(flow).encode()).decode(),
            path=CALLBACK_PATH,
            max_age=FLOW_TTL,
            secure=True,
            httponly=True,
            samesite="Lax",
        )
        return response

    async def verify_callback(self, request: web.Request) -> tuple[int, UUID | None]:
        try:
            raw = request.cookies.get(FLOW_COOKIE, "")
            flow = json.loads(self.flow.decrypt(raw.encode(), ttl=FLOW_TTL))
            state, code = request.query.get("state", ""), request.query.get("code", "")
            require(hmac.compare_digest(state.encode(), flow["state"].encode()), "miniapp_auth")
            require(0 < len(code) <= 2048 and "error" not in request.query, "miniapp_auth")
            invitation = UUID(flow["invitation"]) if flow.get("invitation") else None
            actor = await self.exchange(code, flow["verifier"], flow["nonce"])
            return actor, invitation
        except (
            InvalidToken,
            jwt.PyJWTError,
            ValueError,
            TypeError,
            KeyError,
            aiohttp.ClientError,
            TimeoutError,
        ):
            raise AppError("miniapp_auth") from None

    async def exchange(self, code: str, verifier: str, nonce: str) -> int:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as client:
            async with client.post(
                f"{ISSUER}/token",
                auth=aiohttp.BasicAuth(self.client_id, self.secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.redirect,
                    "client_id": self.client_id,
                    "code_verifier": verifier,
                },
                allow_redirects=False,
            ) as response:
                require(response.status == 200, "miniapp_auth")
                token = (await response.json())["id_token"]
            async with client.get(
                f"{ISSUER}/.well-known/jwks.json", allow_redirects=False
            ) as response:
                response.raise_for_status()
                keys = (await response.json())["keys"]
        return self.decode(token, keys, nonce)

    def decode(self, token: str, keys: list[dict[str, str]], nonce: str) -> int:
        require(isinstance(token, str) and len(token) <= 8192, "miniapp_auth")
        header = jwt.get_unverified_header(token)
        require(header.get("alg") == "RS256" and bool(header.get("kid")), "miniapp_auth")
        matches = [
            key for key in keys if key.get("kid") == header["kid"] and key.get("kty") == "RSA"
        ]
        require(len(matches) == 1, "miniapp_auth")
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(matches[0], algorithm="RS256").key,
            algorithms=["RS256"],
            issuer=ISSUER,
            audience=self.client_id,
            options={"require": ["iss", "aud", "exp", "iat", "sub", "nonce", "id"]},
        )
        require(claims["nonce"] == nonce, "miniapp_auth")
        require(claims.get("azp", self.client_id) == self.client_id, "miniapp_auth")
        actor = claims["id"]
        require(type(actor) is int and 0 < actor < 2**63, "miniapp_auth")
        return int(actor)


def start_session(store: Store, user: User, response: web.Response) -> None:
    ticket = secrets.token_urlsafe(32)
    row = Confirmation(
        user_id=user.id,
        chat_id=user.telegram_id,
        expires_at=now() + timedelta(seconds=SESSION_TTL),
        command=json.dumps(
            {"kind": "browser_session", "digest": hashlib.sha256(ticket.encode()).hexdigest()}
        ),
    )
    store.add(row)
    response.set_cookie(
        SESSION_COOKIE,
        f"{row.id}.{ticket}",
        path="/miniapp/",
        max_age=SESSION_TTL,
        secure=True,
        httponly=True,
        samesite="Lax",
    )


def clear_cookie(response: web.Response, name: str, path: str) -> None:
    response.set_cookie(name, "", path=path, max_age=0, secure=True, httponly=True, samesite="Lax")


async def browser_session(store: Store, request: web.Request) -> Confirmation:
    raw = request.cookies.get(SESSION_COOKIE, "")
    try:
        identifier, ticket = raw.split(".", 1)
        require(len(identifier) == 36 and len(ticket) == 43, "miniapp_auth")
        row = await store.get(Confirmation, UUID(identifier))
        data = json.loads(row.command)
        require(row.expires_at > now() and data.get("kind") == "browser_session", "miniapp_auth")
        digest = hashlib.sha256(ticket.encode()).hexdigest()
        require(hmac.compare_digest(data["digest"], digest), "miniapp_auth")
        return row
    except (ValueError, AppError):
        raise AppError("miniapp_auth") from None
