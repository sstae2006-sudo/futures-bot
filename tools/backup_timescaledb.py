"""``pg_dump``-based backup for the shared team-deployment TimescaleDB.

Not part of the installable package (see ``tools/``'s own role in
``CLAUDE.md`` section 8's File Ownership table) -- an ops script, run by
an operator on the server via cron/Windows Task Scheduler, same category
as ``pull_massive_flatfiles.py``. Reads ``FUTURES_BOT_DATABASE_URL``
(never a CLI argument -- it contains credentials, same convention every
other secret in this project follows) and shells out to the real
``pg_dump`` binary rather than reimplementing a dump in Python, since a
hand-rolled export can never match ``pg_dump``'s own format/consistency
guarantees.

Writes two things on success:

* A timestamped custom-format dump (``pg_dump -Fc``, the compressed,
  ``pg_restore``-only format Postgres itself recommends over plain SQL
  for anything meant to actually be restored) under ``db_backups/``
  (gitignored -- see ``.gitignore``, same convention as
  ``config_backups/``'s config-snapshot role).
* ``db_backups/last_backup.json`` -- a small marker
  (``{"timestamp": ..., "path": ..., "size_bytes": ...}``) that
  ``/api/system/health`` reads for its "last backup" field. Overwritten
  on every successful run; a failed run never touches it, so a stale-but-
  honest "last backup" timestamp is always safer than a fabricated one.

Old dumps are never deleted automatically -- retention is an operator
decision (disk space, compliance, etc.), out of scope for this script.

Verified against the real `deploy/docker-compose.yml` `timescaledb`
service (2026-07-27): `pg_dump` prints a `circular foreign-key
constraints on ... continuous_agg` warning to stderr on every run --
that's TimescaleDB's own internal catalog table, unrelated to this
project's schema, and does not affect the exit code or the dump's
validity; only a nonzero exit code is treated as failure below.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_BACKUP_DIR = Path("db_backups")
MARKER_FILENAME = "last_backup.json"


def _pg_dump_args_from_url(database_url: str) -> list[str]:
    """Translates ``FUTURES_BOT_DATABASE_URL`` (a SQLAlchemy DSN, e.g.
    ``postgresql+psycopg://user:pass@host:5432/dbname``) into ``pg_dump``
    connection flags plus a ``PGPASSWORD``-style environment -- ``pg_dump``
    doesn't understand the ``+psycopg`` driver suffix SQLAlchemy adds, and
    passing the password on the command line would leak it into process
    listings/shell history, so it goes through the environment instead
    (handled by the caller, see ``main``)."""
    parsed = urlparse(database_url.replace("postgresql+psycopg://", "postgresql://", 1))
    if parsed.scheme != "postgresql":
        raise ValueError(f"Expected a postgresql(+psycopg):// URL, got: {database_url!r}")
    args = []
    if parsed.hostname:
        args += ["--host", parsed.hostname]
    if parsed.port:
        args += ["--port", str(parsed.port)]
    if parsed.username:
        args += ["--username", parsed.username]
    dbname = parsed.path.lstrip("/")
    if not dbname:
        raise ValueError(f"Database URL has no database name: {database_url!r}")
    args += [dbname]
    return args, parsed.password


def _write_marker(backup_dir: Path, dump_path: Path) -> None:
    marker = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": str(dump_path),
        "size_bytes": dump_path.stat().st_size,
    }
    (backup_dir / MARKER_FILENAME).write_text(json.dumps(marker, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--backup-dir", type=Path, default=DEFAULT_BACKUP_DIR,
        help=f"Where to write dumps and the marker file (default: {DEFAULT_BACKUP_DIR})",
    )
    args = parser.parse_args(argv)

    database_url = os.environ.get("FUTURES_BOT_DATABASE_URL")
    if not database_url:
        print("FUTURES_BOT_DATABASE_URL is not set -- nothing to back up (this is a team-deployment-only script).", file=sys.stderr)
        return 1

    pg_dump = shutil.which("pg_dump")
    if not pg_dump:
        print(
            "pg_dump not found on PATH. Install the Postgres client tools "
            "(matching the server's major version) and re-run.",
            file=sys.stderr,
        )
        return 1

    try:
        conn_args, password = _pg_dump_args_from_url(database_url)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump_path = args.backup_dir / f"futures_bot_{stamp}.pgdump"

    env = dict(os.environ)
    if password:
        env["PGPASSWORD"] = password

    print(f"Running pg_dump -> {dump_path}")
    result = subprocess.run(
        [pg_dump, "-Fc", "--file", str(dump_path), *conn_args],
        env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"pg_dump failed (exit code {result.returncode}):\n{result.stderr}", file=sys.stderr)
        dump_path.unlink(missing_ok=True)
        return 1

    _write_marker(args.backup_dir, dump_path)
    size_mb = dump_path.stat().st_size / (1024 * 1024)
    print(f"Backup complete: {dump_path} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
