# Privacy Policy — Primetel Health, Monduli Clinic

**Effective date:** _to be set on go-live_
**Controller:** Primetel Health, Monduli Clinic, Arusha, Tanzania
**Data Protection Contact:** _email / phone — to be filled in_

This document explains how Primetel CMS collects, uses, stores, and protects
patient information. It is written to comply with the Tanzania Personal Data
Protection Act, 2022 ("PDPA").

## 1. What we collect

We collect personal and health information you provide during registration
and care. Categories:

- **Identity:** full name, sex, date of birth or estimated age, national ID
- **Contact:** phone, village/ward/district, next-of-kin name and phone
- **Clinical:** chief complaint, symptoms, vitals, diagnoses, prescriptions,
  lab results, attachments, mental-health assessments
- **Operational:** appointment history, invoices and payments
- **Audit:** staff actions on records (logins, reads, edits, exports)

## 2. Why we collect it (lawful basis)

- **Provision of care** (PDPA s.13(c)): required to deliver clinical services.
- **Legal obligation** (PDPA s.13(b)): retention of medical records under
  Tanzanian health regulations.
- **Vital interests** (PDPA s.13(d)): emergency care.
- **Consent** (PDPA s.13(a)): for any non-essential use, e.g. research.

## 3. Who can see your data

Access is role-restricted inside the system:

- **Reception:** identity & contact only
- **Nurses / clinicians:** identity, contact, clinical encounter, vitals,
  diagnoses, labs, prescriptions
- **Pharmacy:** prescriptions and dispensing records for that patient
- **Lab:** lab orders and results
- **Finance:** invoices and payments (no clinical detail)
- **Counsellors:** mental-health encounters; restricted from non-MH staff
- **Admin:** all of the above, with full audit logging

Every access is logged in an immutable audit trail.

## 4. Where data is stored

Data is stored in a managed PostgreSQL database (Supabase, EU/Frankfurt
region) in encrypted form at rest, and transmitted over TLS. Backups are
encrypted and stored off-site for the retention period (see §6).

## 5. Sharing

We do not sell or share patient data with third parties except:

- **Government health reporting** as required by Tanzanian law (e.g. notifiable
  diseases) — anonymised where possible.
- **Lab partners** for send-out tests — only the data necessary to perform
  the test.
- **By patient consent** — for referrals or insurance claims.

## 6. Retention

- **Adult medical records:** retained for **10 years** after last contact.
- **Paediatric records (under 18):** retained until the patient turns 25.
- **Mental health records:** retained for **20 years** after last contact.
- **Financial records:** **7 years** (Tanzanian tax law).
- **Audit logs:** **7 years**.

After the retention period, records are anonymised or securely destroyed.

## 7. Your rights (PDPA Part V)

You may, by written request to the Data Protection Contact:

- ask for a copy of your records
- ask us to correct inaccurate information
- object to processing for non-essential purposes
- ask for deletion (subject to legal retention)
- complain to the Personal Data Protection Commission

We respond within **30 days**.

## 8. Security measures

- Role-based access control with least-privilege defaults
- Encrypted transport (TLS) and at-rest storage
- Idle session timeout (15 minutes) and absolute session lifetime (8 hours)
- Account lockout after 5 failed login attempts
- Full audit log of every read/write/login
- Mental-health records are routed through a separate access policy
- Backups taken nightly and tested quarterly

## 9. Breach notification

If a breach occurs that is likely to harm a patient, we will notify the
Personal Data Protection Commission within **72 hours** and the affected
patients without undue delay, per PDPA s.34.

## 10. Children

For patients under 18, consent is obtained from a parent or legal guardian.

## 11. Changes

Material changes to this policy will be posted at the clinic and notified to
patients at their next visit.
