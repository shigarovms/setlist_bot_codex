"""Configuration guards for the tunnel launcher, without SSH or a bot token."""

import importlib.util
from pathlib import Path

import pytest

from setlist_bot.config import Settings

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run-cloud-sandbox.py"
spec = importlib.util.spec_from_file_location("cloud_sandbox_runner", SCRIPT)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_tunnel_is_loopback_only_and_requires_pinned_host_key():
    command = runner.ssh_command(Path("/private/key"), Path("/private/known_hosts"), 15433)
    assert "127.0.0.1:15433:127.0.0.1:5433" in command
    assert "StrictHostKeyChecking=yes" in command
    assert "BatchMode=yes" in command and "ExitOnForwardFailure=yes" in command
    assert command[-1] == "setlist-tunnel@212.192.217.128"
    assert all("root@" not in part for part in command)


@pytest.mark.parametrize(
    "url,token",
    [
        ("postgresql+asyncpg://u:p@127.0.0.1:15433/setlist", "1110437563:TEST_ONLY"),
        ("postgresql+asyncpg://u:p@public-host:15433/setlist_sandbox", "1110437563:TEST_ONLY"),
        ("postgresql+asyncpg://u:p@127.0.0.1:5432/setlist_sandbox", "1110437563:TEST_ONLY"),
        ("postgresql+asyncpg://u:p@127.0.0.1:15433/setlist_sandbox", "7430933141:TEST_ONLY"),
    ],
)
def test_runner_refuses_production_or_a_database_outside_its_tunnel(url, token):
    settings = Settings(_env_file=None, database_url=url, bot_token=token)
    with pytest.raises(ValueError, match="sandbox token"):
        runner.bot_environment(settings, 15433)


def test_runner_passes_secrets_only_in_child_environment():
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@127.0.0.1:15433/setlist_sandbox",
        bot_token="1110437563:TEST_ONLY",
    )
    environment = runner.bot_environment(settings, 15433)
    assert environment["EXPECTED_BOT_ID"] == "1110437563"
    assert environment["RUNTIME_ENVIRONMENT"] == "sandbox"
    assert environment["RUNTIME_LOCATION"] == "codex-cloud-over-ssh"
