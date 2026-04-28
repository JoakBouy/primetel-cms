# Data Breach Response Playbook

A breach is any unauthorised access, disclosure, alteration, loss, or
destruction of patient information. The legal clock for notifying the
regulator under PDPA s.34 is **72 hours** from awareness.

## Roles

- **Incident Lead:** Clinic Admin (default) — owns the response.
- **Data Protection Contact:** named in the privacy policy.
- **Technical:** the engineer holding root access to the database.

## Step 1 — Detect (T+0)

A breach may surface from:

- An alert from Sentry, Render, or Supabase
- A user report (a patient complaint, a staff member noticing odd activity)
- An audit-log review showing access outside expected hours/roles
- A lost or stolen device

Open an incident ticket immediately. Treat suspected breaches as real until
disproved.

## Step 2 — Contain (T+0 to T+1h)

- Disable or rotate any compromised credentials (`python manage.py
  changepassword <user>`, then force logout via session deletion).
- If the breach involves the database, take a forensic snapshot before any
  remediation: `python manage.py backup_db --output incident-<ts>.sql`.
- If the breach is via the web app, take it offline if necessary
  (Render: pause service).

## Step 3 — Assess (T+1h to T+12h)

Document, in the incident ticket:

- What kind of data was exposed? (identity, contact, clinical, MH, financial)
- How many patients are affected? List `patient_number`s.
- When did it happen, and when did we become aware?
- Could it cause harm — physical, financial, reputational?
- Who had access to the data after the breach?

Pull supporting evidence from `core.AuditLog`, `simple_history` tables,
and Render request logs.

## Step 4 — Notify (within 72 hours)

If the breach is "likely to result in harm" (PDPA s.34):

- Notify the **Personal Data Protection Commission**: written notice
  including the description of the breach, categories of data, approx.
  number of patients, contact details, likely consequences, mitigation
  taken.
- Notify the **affected patients** — by SMS where contact info is
  available, supplemented by a notice posted at the clinic.
- Notify any **partner labs** or insurers whose data is in scope.

If the breach is unlikely to result in harm, document the reasoning and
keep the record on file.

## Step 5 — Remediate

- Patch the underlying vulnerability.
- If credentials were leaked, force a password reset for all affected
  accounts and rotate the SECRET_KEY (which invalidates sessions).
- Update the access-control matrix or middleware as indicated.
- Add a regression test where applicable.

## Step 6 — Post-mortem

Within **two weeks**:

- Hold a blame-free post-mortem with the clinical and technical teams.
- Update this playbook if any step proved unclear.
- Record lessons learned in the clinic's quality-improvement register.

## Communications templates

Templates for SMS, written notice, and a regulator letter live alongside
this playbook in `docs/compliance/templates/` (placeholders to be filled
during go-live preparation).
