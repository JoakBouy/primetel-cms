"""
Database backup command. Wraps `pg_dump` for Postgres and falls back to
copying the SQLite file in dev. Designed to be invoked from cron or
Render's Scheduled Jobs.

Usage:
    python manage.py backup_db                    # writes to BACKUP_DIR
    python manage.py backup_db --output /tmp/x.sql

The output is *not* encrypted by this command — pipe to gpg or hand off to a
storage layer (e.g. supabase storage with a server-side encryption key) to
satisfy data-protection requirements.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Dump the database to a file (pg_dump for Postgres, file copy for SQLite)."

    def add_arguments(self, parser):
        parser.add_argument("--output", help="Output file path (default: backups/<timestamp>.sql)")

    def handle(self, *args, **options):
        db = settings.DATABASES["default"]
        engine = db["ENGINE"]
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        backup_dir = Path(getattr(settings, "BACKUP_DIR", Path(settings.BASE_DIR) / "backups"))
        backup_dir.mkdir(parents=True, exist_ok=True)

        if "sqlite" in engine:
            output = Path(options["output"]) if options["output"] else backup_dir / f"db-{timestamp}.sqlite3"
            shutil.copy2(db["NAME"], output)
            self.stdout.write(self.style.SUCCESS(f"SQLite copied -> {output}"))
            return

        if "postgresql" not in engine:
            raise CommandError(f"Unsupported DB engine: {engine}")

        output = Path(options["output"]) if options["output"] else backup_dir / f"db-{timestamp}.sql.gz"

        # Build pg_dump command from connection params.
        # Prefer DATABASE_URL if provided (Render/Supabase pattern).
        url = os.environ.get("DATABASE_URL")
        if url:
            parsed = urlparse(url)
            env = {
                **os.environ,
                "PGPASSWORD": parsed.password or "",
            }
            pg_dump_cmd = [
                "pg_dump",
                "-h", parsed.hostname or "localhost",
                "-p", str(parsed.port or 5432),
                "-U", parsed.username or "postgres",
                "-d", (parsed.path or "/").lstrip("/"),
                "--no-owner", "--no-acl", "--clean", "--if-exists",
            ]
        else:
            env = {
                **os.environ,
                "PGPASSWORD": db.get("PASSWORD", ""),
            }
            pg_dump_cmd = [
                "pg_dump",
                "-h", db.get("HOST") or "localhost",
                "-p", str(db.get("PORT") or 5432),
                "-U", db.get("USER") or "postgres",
                "-d", db["NAME"],
                "--no-owner", "--no-acl", "--clean", "--if-exists",
            ]

        try:
            with open(output, "wb") as fp:
                proc = subprocess.run(
                    pg_dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=env, check=True,
                )
                # Gzip in-process so we don't depend on shell pipes.
                import gzip
                fp.write(gzip.compress(proc.stdout))
        except FileNotFoundError as e:
            raise CommandError(f"pg_dump not found on PATH: {e}")
        except subprocess.CalledProcessError as e:
            raise CommandError(f"pg_dump failed: {e.stderr.decode(errors='replace')}")

        self.stdout.write(self.style.SUCCESS(f"Postgres dump -> {output} ({output.stat().st_size} bytes)"))
