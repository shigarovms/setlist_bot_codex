"""Run a sandbox bot against the VPS database through a supervised SSH tunnel.

RU: Секреты передаются дочернему процессу через окружение, не через аргументы.
Обрыв SSH останавливает бота; запасная локальная база никогда не подставляется.
EN: Foreground supervisor. A lost tunnel terminates polling; no database fallback.
"""

import argparse
import asyncio
import os
import signal
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from setlist_bot.config import Settings

BOT_ID = 1110437563


def ssh_command(identity: Path, known_hosts: Path, port: int) -> list[str]:
    return [
        "ssh",
        "-F",
        "/dev/null",
        "-N",
        "-T",
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "UserKnownHostsFile=" + str(known_hosts),
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=10",
        "-o",
        "ServerAliveCountMax=2",
        "-L",
        f"127.0.0.1:{port}:127.0.0.1:5433",
        "setlist-tunnel@212.192.217.128",
    ]


def bot_environment(settings: Settings, port: int) -> dict[str, str]:
    url = make_url(settings.database_url)
    token = settings.bot_token.get_secret_value() if settings.bot_token else ""
    if (
        url.drivername != "postgresql+asyncpg"
        or url.database != "setlist_sandbox"
        or url.host != "127.0.0.1"
        or url.port != port
        or token.split(":", 1)[0] != str(BOT_ID)
    ):
        raise ValueError("Use the sandbox token and 127.0.0.1 tunnel endpoint for setlist_sandbox")
    return {
        **os.environ,
        "DATABASE_URL": settings.database_url,
        "BOT_TOKEN": token,
        "EXPECTED_BOT_ID": str(BOT_ID),
        "RUNTIME_ENVIRONMENT": "sandbox",
        "RUNTIME_LOCATION": "codex-cloud-over-ssh",
    }


async def terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=20)
        except TimeoutError:
            process.kill()
            await process.wait()


async def run(args: argparse.Namespace) -> int:
    for path in (args.env_file, args.identity, args.known_hosts):
        if not path.is_file():
            raise ValueError(f"Required private configuration file missing: {path.name}")
    settings = Settings(_env_file=args.env_file)
    env = bot_environment(settings, args.local_port)
    ssh = await asyncio.create_subprocess_exec(
        *ssh_command(args.identity, args.known_hosts, args.local_port)
    )
    bot = None
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stopped.set)
    try:
        for _ in range(50):
            if ssh.returncode is not None or stopped.is_set():
                return 1
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", args.local_port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("SSH tunnel did not start; bot was not started")
        command = [sys.executable, "-m", "setlist_bot"]
        if args.check_only:
            command.append("--check")
        bot = await asyncio.create_subprocess_exec(*command, env=env)
        waits = [
            asyncio.create_task(bot.wait()),
            asyncio.create_task(ssh.wait()),
            asyncio.create_task(stopped.wait()),
        ]
        try:
            await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
            return bot.returncode if bot.returncode is not None else 1
        finally:
            for task in waits:
                task.cancel()
            await asyncio.gather(*waits, return_exceptions=True)
    finally:
        if bot is not None:
            await terminate(bot)
        await terminate(ssh)
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument(
        "--local-port", type=int, choices=range(1024, 65536), default=15433, metavar="PORT"
    )
    parser.add_argument("--check-only", action="store_true")
    try:
        raise SystemExit(asyncio.run(run(parser.parse_args())))
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
