#!/usr/bin/env python3
"""Portable offline pytest runner for Windows and Linux."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

RUNNER_DESCRIPTION = """\
Offline Manngo test runner.

Forces FLASK_ENV=testing, SQLite :memory: by default, clears integration
credentials without printing them, blocks outbound sockets/HTTP/AI clients,
and excludes tests/test_supabase_storage_integration.py.

Local loopback PostgreSQL and Redis are opt-in. --allow-local-redis is only
accepted together with --allow-local-postgres and a validated disposable
PostgreSQL DSN; it freezes the exact reserved Redis port for the run.

All unrecognized arguments are passed to pytest.
"""


def _parse_runner_arguments(argv: list[str]):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runner-help", action="store_true")
    parser.add_argument("--no-bytecode", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--allow-local-postgres",
        action="store_true",
        default=None,
        help="Explicitly authorize a disposable manngo_test* PostgreSQL database on loopback.",
    )
    parser.add_argument(
        "--allow-local-redis",
        action="store_true",
        default=None,
        help=(
            "Explicitly authorize the reserved loopback Redis URI. Requires "
            "--allow-local-postgres and a validated local PostgreSQL DSN."
        ),
    )
    return parser.parse_known_args(argv)


def main(argv: list[str] | None = None) -> int:
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    runner_arguments, pytest_arguments = _parse_runner_arguments(raw_arguments)

    if runner_arguments.runner_help:
        print(RUNNER_DESCRIPTION)
        return 0

    if runner_arguments.allow_local_redis and not runner_arguments.allow_local_postgres:
        print(
            "[offline] REFUSED: --allow-local-redis requires "
            "--allow-local-postgres and a validated local PostgreSQL DSN",
            file=sys.stderr,
        )
        return 2

    repository_root = Path(__file__).resolve().parents[1]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    if runner_arguments.no_bytecode:
        sys.dont_write_bytecode = True
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

    from scripts.offline_test_env import (
        UnsafeTestDatabaseError,
        build_pytest_args,
        install_offline_guards,
        prepare_test_environment,
    )

    try:
        status = prepare_test_environment(
            allow_local_postgres=runner_arguments.allow_local_postgres,
            allow_local_redis=runner_arguments.allow_local_redis,
        )
    except UnsafeTestDatabaseError as error:
        print(f"[offline] REFUSED: {error}", file=sys.stderr)
        return 2

    install_offline_guards(
        allow_loopback=status.allow_loopback,
        allow_local_redis=status.allow_local_redis,
    )

    if runner_arguments.check_only:
        print(f"[offline] {status.summary()}")
        print("[offline] network guards installed; application not imported")
        return 0

    pytest_args = build_pytest_args(
        pytest_arguments,
        repository_root=repository_root,
        no_cache=runner_arguments.no_cache,
    )

    print(f"[offline] {status.summary()}")
    print("[offline] network guards installed before pytest/app imports")
    print("[offline] excluded tests/test_supabase_storage_integration.py (real Storage operations)")

    import pytest

    return int(pytest.main(pytest_args))


if __name__ == "__main__":
    raise SystemExit(main())
