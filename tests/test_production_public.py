import base64
import hashlib
import hmac
import importlib.util
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "production_public", ROOT / "scripts/setup-production-public.py"
    )
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    return setup


@pytest.fixture
def installation(tmp_path, monkeypatch):
    setup = load()
    helpers, mini, key = (
        setup.module(name)
        for name in ("setup-sandbox-calendar", "setup-sandbox-miniapp", "calendar_key")
    )
    production, sandbox = tmp_path / "production", tmp_path / "sandbox"
    for root in (production, sandbox):
        (root / "scripts").mkdir(parents=True)
        (root / "deploy").mkdir()
    original = "BOT_TOKEN=7430933141:PRODUCTION_SYNTHETIC\nPOSTGRES_PASSWORD=PRIVATE_OWNER\n"
    (production / ".env").write_text(original)
    (sandbox / ".env").write_text(
        "CALENDAR_HOST=sandbox.example.org\nSANDBOX_BOT_TOKEN=PRIVATE_SANDBOX_TOKEN\n"
    )
    (sandbox / ".env.admin").write_text("WEB_ADMIN_ALLOW_PRODUCTION=true\nPASSWORD=PRIVATE_ADMIN\n")
    (sandbox / ".env.miniapp").write_text("MINIAPP_AUTH_KEY=PRIVATE_SANDBOX_KEY\n")
    for filename in ("compose.proxy.production-access.yaml", "deploy/Caddyfile.public"):
        (sandbox / filename).write_text("synthetic test source\n")
    monkeypatch.chdir(production)
    monkeypatch.setattr(setup, "ROOT", production)
    monkeypatch.setattr(setup, "SANDBOX", sandbox)
    monkeypatch.setattr(setup, "__file__", str(production / "scripts/setup-production-public.py"))
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr("sys.argv", ["setup", "--host", "production.example.org"])
    operations, statements, created = [], [], set()

    def run(args, **kwargs):
        operations.append((args, kwargs.get("cwd", production)))
        return SimpleNamespace(stdout="main\n" if args[:2] == ["git", "branch"] else "")

    def sql(statement):
        statements.append(statement)
        if "bot_id::text" in statement:
            return "7430933141:production"
        if "version_num" in statement:
            return setup.SCHEMA
        if "pg_roles" in statement:
            if "json_build_array" in statement:
                return "[false,false,false,false,false]"
            return "t" if any("rolname='" + role + "'" in statement for role in created) else "f"
        if "token_ciphertext <>" in statement:
            return "f"
        assert "setlist_sandbox" not in statement
        if statement.startswith("BEGIN"):
            assert "ON DATABASE setlist" in statement and "GRANT" in statement
            created.update(re.findall(r"CREATE ROLE ([a-z_]+)", statement))
        return ""

    def https(origin):
        operations.append((["HTTPS", origin], production))

    monkeypatch.setattr(setup, "run", run)
    monkeypatch.setattr(setup, "sql", sql)
    monkeypatch.setattr(mini, "wait_for_https", https)
    monkeypatch.setattr(
        setup,
        "module",
        lambda name: {
            "setup-sandbox-calendar": helpers,
            "setup-sandbox-miniapp": mini,
            "calendar_key": key,
        }[name],
    )
    return SimpleNamespace(
        setup=setup,
        production=production,
        sandbox=sandbox,
        original=original,
        operations=operations,
        statements=statements,
        mini=mini,
    )


def test_production_setup_uses_separate_credentials_and_shared_http_proxy(installation, capsys):
    env = installation
    env.setup.main()
    mini = (env.production / ".env.miniapp").read_text()
    calendar = (env.production / ".env.calendar").read_text()
    expected = base64.urlsafe_b64encode(
        hmac.new(b"WebAppData", b"7430933141:PRODUCTION_SYNTHETIC", hashlib.sha256).digest()
    ).decode()
    assert "MINIAPP_AUTH_KEY=" + expected in mini and "MINIAPP_ENVIRONMENT=production" in mini
    assert "@db:5432/setlist\n" in mini and "@db:5432/setlist\n" in calendar
    assert "7430933141" in mini and "7430933141" in calendar
    for contents in (mini, calendar):
        assert "PRIVATE_SANDBOX" not in contents and "PRODUCTION_SYNTHETIC" not in contents
        assert "PRIVATE_OWNER" not in contents and "PRIVATE_ADMIN" not in contents
    assert (env.production / ".env.miniapp").stat().st_mode & 0o777 == 0o600
    assert (env.production / ".env.calendar-key").stat().st_mode & 0o777 == 0o600
    assert "MINIAPP_AUTH_KEY=PRIVATE_SANDBOX_KEY" in (env.sandbox / ".env.miniapp").read_text()
    assert (
        env.sandbox / ".env.production-public"
    ).read_text() == "PRODUCTION_PUBLIC_HOST=production.example.org\n"
    assert "PRIVATE_SANDBOX_TOKEN" in (env.sandbox / ".env").read_text()
    operations = env.operations
    start = next(i for i, (args, _) in enumerate(operations) if "up" in args and args[-1] == "bot")
    assert all(i < start for i, (args, _) in enumerate(operations) if args[0] == "HTTPS")
    proxy = next(
        (args, cwd) for args, cwd in operations if "up" in args and args[-1] == "calendar_proxy"
    )
    assert proxy[1] == env.sandbox and "--no-deps" in proxy[0]
    assert "compose.proxy.production-access.yaml" in proxy[0]
    assert "compose.admin.production-access.yaml" in proxy[0]
    assert not any("pg_restore" in args or "down" in args for args, _ in operations)
    assert "PRODUCTION_SYNTHETIC" not in capsys.readouterr().out
    # A retry preserves database passwords and the calendar encryption key.
    private = (mini, calendar, (env.production / ".env.calendar-key").read_text())
    env.setup.main()
    assert private == (
        (env.production / ".env.miniapp").read_text(),
        (env.production / ".env.calendar").read_text(),
        (env.production / ".env.calendar-key").read_text(),
    )


def test_wrong_production_identity_stops_before_configuration_writes(installation, monkeypatch):
    env = installation
    monkeypatch.setattr(env.setup, "sql", lambda _: "1110437563:sandbox")
    with pytest.raises(SystemExit, match="database identity"):
        env.setup.main()
    assert not (env.production / ".env.miniapp").exists()
    assert not any("up" in args for args, _ in env.operations)


def test_https_failure_leaves_production_bot_configuration_unchanged(installation, monkeypatch):
    env = installation

    def unavailable(origin):
        raise SystemExit("HTTPS not ready")

    monkeypatch.setattr(env.mini, "wait_for_https", unavailable)
    with pytest.raises(SystemExit, match="HTTPS not ready"):
        env.setup.main()
    assert (env.production / ".env").read_text() == env.original
    assert not any("up" in args and args[-1] == "bot" for args, _ in env.operations)


def test_same_hostname_is_rejected_before_any_key_creation(installation, monkeypatch):
    env = installation
    monkeypatch.setattr("sys.argv", ["setup", "--host", "sandbox.example.org"])
    with pytest.raises(SystemExit, match="distinct"):
        env.setup.main()
    assert not (env.production / ".env.calendar-key").exists()
