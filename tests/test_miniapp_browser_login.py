import base64
import hashlib
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr
from test_miniapp import KEY, settings, signed
from test_repertoire_workspace_music import deliver_request

from setlist_bot.domain.entities import DeploymentIdentity
from setlist_bot.domain.rules import AppError
from setlist_bot.infrastructure.database import PostgresUnitOfWork
from setlist_bot.miniapp.app import create_app
from setlist_bot.miniapp.browser_login import FLOW_COOKIE, ISSUER, SESSION_COOKIE, BrowserLogin
from setlist_bot.miniapp.settings import MiniAppSettings


def test_oidc_claims_use_signed_numeric_telegram_id():
    config = MiniAppSettings(
        database_url="unused",
        expected_bot_id=1110437563,
        environment="sandbox",
        bot_username="botToPract696_bot",
        auth_key=base64.urlsafe_b64encode(KEY).decode(),
        public_base_url="https://example.org",
        browser_login_secret=SecretStr("test-only"),
    )
    login = BrowserLogin(config)
    signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key = jwt.algorithms.RSAAlgorithm.to_jwk(signer.public_key(), as_dict=True) | {"kid": "test"}
    claims = {
        "iss": ISSUER,
        "aud": login.client_id,
        "nonce": "expected",
        "sub": "4567890",
        "id": 42,
        "iat": int(time.time()) - 10,
        "exp": int(time.time()) + 60,
    }

    def token(**changes):
        return jwt.encode(claims | changes, signer, algorithm="RS256", headers={"kid": "test"})

    assert login.decode(token(), [key], "expected") == 42
    for wrong in ({"aud": "1"}, {"exp": 0}, {"iss": "https://example.org"}):
        with pytest.raises(jwt.InvalidTokenError):
            login.decode(token(**wrong), [key], "expected")
    for wrong in ({"nonce": "unexpected"}, {"id": True}, {"id": "42"}):
        with pytest.raises(AppError, match="miniapp_auth"):
            login.decode(token(**wrong), [key], "expected")
    other_signer = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(claims, other_signer, algorithm="RS256", headers={"kid": "test"})
    with pytest.raises(jwt.InvalidSignatureError):
        login.decode(forged, [key], "expected")


@pytest.mark.usefixtures("invited_member")
async def test_web_login_uses_same_account_as_mini_app_and_logout_revokes(
    engine, world, monkeypatch
):
    config = settings(engine).model_copy(update={"browser_login_secret": SecretStr("test-only")})
    async with PostgresUnitOfWork(engine) as uow:
        uow.store.add(DeploymentIdentity(bot_id=1110437563, environment="sandbox"))

    calls = []

    async def exchange(_login, code, verifier, nonce):
        calls.append((code, verifier, nonce))
        return 2 if code == "known" else 987654321

    monkeypatch.setattr(BrowserLogin, "exchange", exchange)
    async with TestClient(TestServer(create_app(config, engine))) as client:
        begin = await client.get("/miniapp/auth/login", allow_redirects=False)
        query = parse_qs(urlsplit(begin.headers["Location"]).query)
        assert begin.status == 303 and query["scope"] == ["openid profile"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == ["https://example.org/miniapp/auth/callback"]
        flow = begin.cookies[FLOW_COOKIE]
        assert flow["secure"] and flow["httponly"] and flow["samesite"] == "Lax"
        endpoint = "/miniapp/auth/callback"
        cookie = {"Cookie": f"{FLOW_COOKIE}={flow.value}"}
        params = {"state": query["state"][0], "code": "known"}
        bad = await client.get(
            endpoint, params=params | {"state": "false"}, headers=cookie, allow_redirects=False
        )
        assert bad.headers["Location"].endswith("login=failed") and not calls
        unknown = await client.get(
            endpoint, params=params | {"code": "unknown"}, headers=cookie, allow_redirects=False
        )
        assert unknown.headers["Location"].endswith("login=start_bot")
        assert SESSION_COOKIE not in unknown.cookies
        success = await client.get(endpoint, params=params, headers=cookie, allow_redirects=False)
        assert success.headers["Location"] == "/miniapp/"
        _, verifier, nonce = calls[-1]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        assert query["code_challenge"] == [challenge.decode().rstrip("=")]
        assert query["nonce"] == [nonce]

        session = success.cookies[SESSION_COOKIE]
        assert session["httponly"] and session["secure"] and session["path"] == "/miniapp/"
        web_headers = {"Cookie": f"{SESSION_COOKIE}={session.value}"}
        url = "/miniapp/api/bands"
        browser = await (await client.get(url, headers=web_headers)).json()
        telegram = await (
            await client.get(url, headers={"Authorization": "tma " + signed(actor=2)})
        ).json()
        assert browser == telegram
        assert browser["bands"] == [{"id": str(world.group.id), "name": world.group.name}]
        assert (
            await client.get(url, headers=web_headers | {"Authorization": "tma forged"})
        ).status == 401
        same_origin = web_headers | {"Origin": "https://example.org"}
        editor = await client.post(
            "/miniapp/api/open_draft",
            headers=same_origin,
            json={"band": str(world.group.id), "performance": str(world.concert.id)},
        )
        assert editor.status == 200
        state_url = "/miniapp/api/state?session=" + (await editor.json())["session"]
        assert (await client.get(state_url, headers=web_headers)).status == 200
        miniapp_headers = {"Authorization": "tma " + signed(actor=2)}
        assert (await client.get(state_url, headers=miniapp_headers)).status == 200
        account = await client.post("/miniapp/api/open_account", headers=same_origin, json={})
        assert account.status == 200
        profile_update = await client.post(
            "/miniapp/api/profile_action",
            headers=same_origin,
            json={
                **(await account.json()),
                "action": "profile_save",
                "version": 0,
                "patch": {"name": "Имя из браузера"},
                "request_id": str(uuid4()),
            },
        )
        assert profile_update.status == 200
        assert await deliver_request(engine, (await profile_update.json())["request"])
        profile_url = "/miniapp/api/profile"
        profile = await (await client.get(profile_url, headers=web_headers)).json()
        assert profile["profile"]["name"] == "Имя из браузера"
        assert profile == await (await client.get(profile_url, headers=miniapp_headers)).json()
        sign_out = "/miniapp/auth/logout"
        assert (await client.post(sign_out, headers=web_headers)).status == 401
        assert (await client.post(sign_out, headers=same_origin)).status == 200
        assert (await client.get(url, headers=web_headers)).status == 401
