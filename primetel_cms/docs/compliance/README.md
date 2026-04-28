# Compliance documentation

These documents are the policy layer that wraps the technical controls in
Primetel CMS. Read them before putting real patient data into the system.

| File                   | Purpose                                                      |
| ---------------------- | ------------------------------------------------------------ |
| `PRIVACY_POLICY.md`    | Patient-facing notice. Display at reception; link from app.  |
| `RETENTION_SCHEDULE.md`| How long each record type is kept and how it is destroyed.   |
| `CONSENT_FORM.md`      | Printable consent collected at registration.                 |
| `BREACH_PLAYBOOK.md`   | What to do when something goes wrong. PDPA 72-hour clock.    |

## Pre-go-live checklist

- [ ] Privacy policy reviewed by clinic admin and a Tanzanian legal advisor.
- [ ] Data Protection Contact name and email filled in across all four
      documents.
- [ ] Consent form printed in Swahili and English; staff trained to use it.
- [ ] Backups demonstrated end-to-end: `python manage.py backup_db` creates
      an artefact, restore tested in a staging environment.
- [ ] Incident ticket template created in your tracker, linked from the
      breach playbook.
- [ ] Roles seeded (`python manage.py seed_data`) and assigned to staff.
- [ ] Idle session timeout (15 min) verified at every workstation.
- [ ] Sentry DSN set in production OR an alternative error stream
      established.
- [ ] Pilot phase planned: 2-4 weeks of paper + digital double-entry before
      retiring paper.
