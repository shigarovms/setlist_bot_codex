import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[1] / f"scripts/setup-sandbox-{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("https_ok", [True, False])
def test_setup_limits_scope_keeps_secrets_private_and_checks_https_before_bot(
    tmp_path, monkeypatch, capsys, https_ok
):
    setup, helpers = load("miniapp"), load("calendar")
    (tmp_path / "scripts").mkdir()
    (tmp_path / ".env").write_text(
        "SANDBOX_BOT_TOKEN=1110437563:SYNTHETIC\n"
        "CALENDAR_HOST=test.example.org\nCALENDAR_PUBLIC_BASE_URL=https://test.example.org\n"
    )
    (tmp_path / ".env.calendar").write_text("CALENDAR_DATABASE_URL=PRIVATE_CALENDAR\n")
    (tmp_path / ".env.admin").write_text("WEB_ADMIN_ALLOW_PRODUCTION=true\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "__file__", str(tmp_path / "scripts/setup-sandbox-miniapp.py"))
    monkeypatch.setattr(setup.os, "geteuid", lambda: 0)
    operations = []

    def run(args, **kwargs):
        operations.append(args)
        return SimpleNamespace(stdout="", returncode=0)

    def sql(statement):
        if "version_num" in statement:
            return setup.SCHEMA
        if "bot_id::text" in statement:
            return "1110437563:sandbox"
        if "pg_roles" in statement:
            return "f"
        assert "NOBYPASSRLS" in statement and "UPDATE(content,version)" in statement
        assert "ON DATABASE setlist_sandbox" in statement
        assert "ON DATABASE setlist;" not in statement
        operations.append(["grants"])
        return ""

    class HTTPS:
        status = 200

        def __enter__(self):
            operations.append(["HTTPS"])
            if not https_ok:
                raise OSError("unavailable")
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(
        setup,
        "calendar_helpers",
        lambda: SimpleNamespace(
            run=run,
            sql=sql,
            values=helpers.values,
            private_write=helpers.private_write,
        ),
    )
    monkeypatch.setattr(setup.subprocess, "run", run)
    monkeypatch.setattr(setup, "urlopen", lambda *args, **kwargs: HTTPS())
    if https_ok:
        setup.main()
    else:
        with pytest.raises(OSError):
            setup.main()
    private = (tmp_path / ".env.miniapp").read_text()
    assert "MINIAPP_AUTH_KEY=" in private and "SYNTHETIC" not in private
    assert "PRIVATE_CALENDAR" not in private and "BOT_TOKEN=" not in private
    assert (tmp_path / ".env.miniapp").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / ".env.calendar").read_text() == "CALENDAR_DATABASE_URL=PRIVATE_CALENDAR\n"
    assert "SYNTHETIC" not in capsys.readouterr().out
    assert all("compose.production.yaml" not in args for args in operations)
    bot = [i for i, args in enumerate(operations) if "up" in args and args[-1] == "bot"]
    assert bool(bot) == https_ok
    if https_ok:
        assert operations.index(["HTTPS"]) < bot[0]
        assert "compose.admin.production-access.yaml" in operations[bot[0]]
