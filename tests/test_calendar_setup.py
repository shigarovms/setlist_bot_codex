import importlib.util
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest


def load():
    spec = importlib.util.spec_from_file_location(
        "calendar_setup", Path(__file__).parents[1] / "scripts/setup-sandbox-calendar.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_setup_keeps_production_and_credentials_separate_and_enables_bot_only_after_https(
    tmp_path, monkeypatch, capsys
):
    setup = load()
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".env").write_text("SANDBOX_BOT_TOKEN=1110437563:SYNTHETIC\n")
    admin = (
        "WEB_ADMIN_ALLOW_PRODUCTION=true\nWEB_ADMIN_PRODUCTION_DATABASE_URL=PRIVATE_PRODUCTION\n"
    )
    (tmp_path / ".env.admin").write_text(admin)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "__file__", str(tmp_path / "scripts/setup-sandbox-calendar.py"))
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    monkeypatch.setattr("sys.argv", ["setup-sandbox-calendar.py"])
    operations = []

    def run(args, **kwargs):
        operations.append(args)
        return SimpleNamespace(stdout="")

    def sql(statement):
        if "version_num" in statement:
            return setup.SCHEMA
        if "bot_id::text" in statement:
            return "1110437563:sandbox"
        if "pg_roles" in statement:
            return "f"
        assert "NOBYPASSRLS" in statement and "GRANT SELECT" in statement
        assert "personal_drafts" not in statement and "ON users" not in statement
        assert (
            "ON DATABASE setlist_sandbox" in statement and "ON DATABASE setlist;" not in statement
        )
        return ""

    def https(*args, **kwargs):
        operations.append(["HTTPS verified"])
        raise HTTPError(args[0], 404, "Not found", {}, None)

    monkeypatch.setattr(setup, "run", run)
    monkeypatch.setattr(setup, "sql", sql)
    monkeypatch.setattr(setup, "urlopen", https)
    setup.main()
    private = (tmp_path / ".env.calendar").read_text()
    assert "CALENDAR_DATABASE_URL" in private and "BOT_TOKEN=" not in private
    assert (tmp_path / ".env.calendar").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".env.admin").read_text() == admin
    output = capsys.readouterr().out
    assert "SYNTHETIC" not in output and "PRIVATE_PRODUCTION" not in output
    https_index = operations.index(["HTTPS verified"])
    bot_index = next(
        index
        for index, command in enumerate(operations)
        if "up" in command and command[-1] == "bot"
    )
    assert https_index < bot_index
    assert all("compose.production.yaml" not in command for command in operations)


def test_setup_rejects_non_hostname_before_creating_files(tmp_path, monkeypatch):
    setup = load()
    monkeypatch.setattr("sys.argv", ["setup", "--host", "https://attacker.example/path"])
    with pytest.raises(SystemExit, match="DNS hostname"):
        setup.main()
