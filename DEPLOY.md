# Deploying to Render

Step-by-step for a fresh Render account. Assumes the repo is on GitHub and
the new Render account has been granted access to it.

## 0. Prerequisites

- A Supabase project with a Postgres database. Get the **Session pooler**
  URL (port 5432) from *Project Settings → Database → Connection string →
  URI*. The username includes the project ref, e.g.
  `postgres.<project-ref>`. Reset the database password if needed.
- A GitHub repo holding this codebase, accessible to the Render account.

## 1. Create the Blueprint

1. Render dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo.
3. Render reads `render.yaml` from the repo root and proposes one web
   service: `primetel-cms`. The blueprint sets `rootDir: primetel_cms`, so
   builds run from inside that subdirectory automatically.
4. Click **Apply**.

## 2. Set the secrets it asks for

Render shows env vars marked `sync: false` for you to fill in. **Set
these before the first build** — the build creates your admin account
from them.

| Key                         | Value                                                                                                                           |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `DATABASE_URL`              | Supabase session-pooler URL: `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres`           |
| `BOOTSTRAP_ADMIN_PASSWORD`  | A strong password for the initial admin user. Used **once** on first build; later changes to this env var are ignored.          |
| `BOOTSTRAP_ADMIN_EMAIL`     | Email for the initial admin user.                                                                                               |
| `SENTRY_DSN`                | Optional. Paste a Sentry DSN to turn on error reporting. Leave blank otherwise — the app handles a missing DSN gracefully.      |

`SECRET_KEY` is `generateValue: true`, so Render creates one for you.
`BOOTSTRAP_ADMIN_USERNAME` defaults to `admin`; change it in the dashboard
if you want.

`ALLOWED_HOSTS` defaults to `primetel-cms.onrender.com`. If your service is
named anything else, or you bind a custom domain, edit this env var
*before* the first deploy or the app will 400 on every request. The Render
hostname `*.onrender.com` is also auto-added at runtime via
`RENDER_EXTERNAL_HOSTNAME`, so you have a safety net.

## 3. First deploy

Click **Deploy**. The build runs:

```
pip install -r requirements.txt
python manage.py collectstatic --noinput
python manage.py migrate --noinput
python manage.py seed_data        # ICD-10, drugs, labs, services, roles
python manage.py bootstrap_admin  # creates admin from BOOTSTRAP_ADMIN_*
gunicorn config.wsgi:application  # (start command)
```

The seed and bootstrap steps are **idempotent** — they're safe to run on
every deploy. `bootstrap_admin` only sets the password the first time;
later builds leave the existing user alone (so editing
`BOOTSTRAP_ADMIN_PASSWORD` later does nothing — change passwords via
`/admin/` or via `manage.py changepassword` if you have a paid plan with
shell access).

`apt.txt` (Pango/Cairo/GDK-Pixbuf) is auto-installed for WeasyPrint so PDFs
render correctly. First build takes 4–6 minutes.

## 4. Sign in

When the deploy goes green, visit `https://<service-name>.onrender.com/login/`
and sign in with the username/password you set in step 2. The user is
already a superuser with the ADMIN role, so all role-decorated views are
reachable immediately.

> **Free tier note:** Render's free plan has no Shell access, so all
> first-deploy administration has to happen via the build pipeline. That's
> exactly what `seed_data` and `bootstrap_admin` are for. If you upgrade
> later, you can use Shell directly for one-offs.

## 5. Backups

**Free tier:** Render's free plan has no Cron Jobs. Use **Supabase's
built-in nightly backups** (Project Settings → Database → Backups).
Supabase keeps 7 daily backups on the free plan; if you need longer
retention, run `python manage.py backup_db` from your laptop on a
schedule (cron / Task Scheduler) and store the output somewhere durable.

**Paid tier:** Add a **Cron Job** in Render:

- Name: `primetel-cms-backup`
- Schedule: `0 2 * * *` (02:00 UTC nightly)
- Command: `python manage.py backup_db`
- Same `rootDir`, same env vars, especially `DATABASE_URL`.

Render Cron Jobs have ephemeral disk; pipe the dump to S3 or Supabase
Storage if you need it offsite.

## 6. Health & monitoring

- **Health check**: Render polls `/healthz/` and restarts the service if
  it stops responding.
- **Free tier sleep**: services on the free plan idle out after 15 minutes
  of no traffic. Set up an external pinger (UptimeRobot, etc.) hitting
  `/healthz/` every 10 minutes if you need warmth.
- **Sentry**: paste a DSN into `SENTRY_DSN` to start receiving error
  reports. PHI is not sent (`send_default_pii=False`).

## 7. Custom domain (optional)

When ready:

1. Render dashboard → service → **Settings** → **Custom Domains** → add
   your domain.
2. Update DNS as instructed (CNAME or ALIAS to your Render URL).
3. Append the new domain to `ALLOWED_HOSTS`:
   `primetel-cms.onrender.com,cms.example.tz`
4. Redeploy.

## 8. Common issues

- **400 Bad Request on every page**: `ALLOWED_HOSTS` doesn't include the
  hostname you visited. Check the env var.
- **`AttributeError: 'list' object has no attribute 'split'`**: you're on
  an old version of `prod.py`. Pull latest — this was fixed.
- **Database connection refused / IPv6 errors**: you used the
  *Direct connection* URL instead of the *Session pooler*. Render is
  IPv4-only; Supabase direct is IPv6-only. Switch to the session pooler
  (port 5432).
- **`select_for_update` errors / weird transaction failures**: you're on
  the *Transaction pooler* (port 6543). It doesn't support row-level
  locks. Switch to *Session pooler* (port 5432).
- **PDFs come back as HTML**: the `apt.txt` packages didn't install, so
  WeasyPrint fell back. Check the build log for apt errors.
- **CSRF errors after login**: check `SECURE_PROXY_SSL_HEADER` is honoured
  (it is, in `prod.py`) and that the URL you're hitting is HTTPS.
- **Can't log in / forgot bootstrap password**: editing
  `BOOTSTRAP_ADMIN_PASSWORD` doesn't reset an existing user — that's
  intentional, otherwise anyone with build access could overwrite the
  password. To recover, delete the row in Supabase → SQL Editor:
  ```sql
  DELETE FROM accounts_user WHERE username='admin';
  ```
  Then redeploy with the new `BOOTSTRAP_ADMIN_PASSWORD` set, and
  `bootstrap_admin` will recreate the superuser. (On a paid plan, use
  `python manage.py changepassword admin` from the Render Shell instead.)
