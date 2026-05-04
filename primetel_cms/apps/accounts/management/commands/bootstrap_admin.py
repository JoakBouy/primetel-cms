"""
Idempotent superuser bootstrap for environments where you can't open a shell
(e.g. Render free tier).

Reads three env vars:

    BOOTSTRAP_ADMIN_USERNAME   default: admin
    BOOTSTRAP_ADMIN_EMAIL      default: admin@example.com
    BOOTSTRAP_ADMIN_PASSWORD   required to do anything; if unset, the command
                               is a no-op (so it's safe to leave in the build
                               pipeline forever).

Behaviour:

- If no user with that username exists, create a superuser and assign the
  ADMIN role.
- If the user already exists, leave the password alone and just ensure
  is_superuser/is_staff/role are correct. We deliberately do NOT reset the
  password on every deploy — that would let anyone with build access take
  over the account.

Never raises: any failure is logged and exits 0, so a misconfigured env var
can't break deploys.
"""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update an initial superuser from environment variables."

    def handle(self, *args, **options):
        password = os.environ.get("BOOTSTRAP_ADMIN_PASSWORD")
        if not password:
            self.stdout.write("BOOTSTRAP_ADMIN_PASSWORD not set; skipping.")
            return

        username = os.environ.get("BOOTSTRAP_ADMIN_USERNAME", "admin")
        email = os.environ.get("BOOTSTRAP_ADMIN_EMAIL", "admin@example.com")

        try:
            User = get_user_model()
            user = User.objects.filter(username=username).first()

            # Try to attach the ADMIN role if the table is seeded.
            admin_role = None
            try:
                from apps.accounts.models import Role
                admin_role = Role.objects.filter(code="ADMIN").first()
            except Exception:
                pass

            if user is None:
                user = User.objects.create_superuser(
                    username=username, email=email, password=password
                )
                if admin_role:
                    user.role = admin_role
                if hasattr(user, "full_name") and not user.full_name:
                    user.full_name = "Administrator"
                user.save()
                self.stdout.write(self.style.SUCCESS(f"Created superuser {username}"))
            else:
                changed = False
                if not user.is_superuser:
                    user.is_superuser = True
                    changed = True
                if not user.is_staff:
                    user.is_staff = True
                    changed = True
                if admin_role and getattr(user, "role_id", None) != admin_role.pk:
                    user.role = admin_role
                    changed = True
                if changed:
                    user.save()
                    self.stdout.write(self.style.SUCCESS(f"Updated superuser {username}"))
                else:
                    self.stdout.write(f"Superuser {username} already configured.")
        except Exception as exc:
            # Never break deploy on a bootstrap hiccup.
            self.stdout.write(self.style.WARNING(f"bootstrap_admin skipped: {exc}"))
