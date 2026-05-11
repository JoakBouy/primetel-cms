"""
Wipe operational + catalogue data; keep only users + roles.

Usage:
    python manage.py factory_reset                          # dry-run
    python manage.py factory_reset --confirm --i-mean-it    # wipe (prod)
    python manage.py factory_reset --confirm                # wipe (non-prod)

This shares logic with the admin-panel button — both call into
`apps.core.factory_reset`. See that module for the deletion order and
the history-table handling.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.core.factory_reset import (
    looks_like_production, plan_factory_reset, run_factory_reset,
)


class Command(BaseCommand):
    help = "Wipe operational + catalogue data, keeping users and roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--confirm",
            action="store_true",
            help="Actually delete. Without this, runs as a dry-run.",
        )
        parser.add_argument(
            "--i-mean-it",
            action="store_true",
            help="Required when ALLOWED_HOSTS looks like production.",
        )

    def handle(self, *args, **options):
        plan, live_total, history_total, kept = plan_factory_reset()

        self.stdout.write(self.style.NOTICE("Factory reset plan:"))
        for app_label, model_name, live, history in plan:
            note = f" (+ {history} history rows)" if history else ""
            self.stdout.write(f"  delete  {app_label}.{model_name:30s} {live:>6d}{note}")
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  keep    accounts.User  {kept['users']}"))
        self.stdout.write(self.style.SUCCESS(f"  keep    accounts.Role  {kept['roles']}"))
        self.stdout.write("")
        self.stdout.write(f"Total rows to delete: {live_total + history_total}")

        if not options["confirm"]:
            self.stdout.write(self.style.WARNING(
                "\nDRY RUN. No changes made. Re-run with --confirm to wipe."
            ))
            return

        if looks_like_production() and not options["i_mean_it"]:
            raise CommandError(
                "Refusing to run: ALLOWED_HOSTS looks like production. "
                "If you really mean it, add --i-mean-it."
            )

        summary = run_factory_reset()
        self.stdout.write(self.style.SUCCESS("\nWiped:"))
        for label, live, hist in summary:
            note = f" (+ {hist} history)" if hist else ""
            self.stdout.write(f"  {label:40s} {live} rows{note}")
        self.stdout.write(self.style.SUCCESS("\nDone. Users and roles preserved."))
