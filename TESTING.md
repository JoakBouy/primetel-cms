# Primetel CMS — Manual E2E Test Plan

Run this checklist before each release. Aim for ~45 minutes with 2 testers
sharing the work (one as a clinical role, one as front-desk / lab). Mark any
failure with the date, role, and what you saw.

## Setup

You need user accounts in **every** role for this to work end-to-end. If
you don't have them yet, create one of each via Django admin (`/admin/`):

- `admin` (ADMIN or superuser)
- `recep` (RECEPTIONIST — also covers FINANCE)
- `nurse` (NURSE)
- `clin` (CLINICIAN)
- `pharm` (PHARMACY)
- `lab` (LAB)
- `couns` (COUNSELLOR — optional, only if you do mental health)

Have at least:

- 1 lab test in the catalogue (any active test with a numeric reference range so
  flagging is exercised)
- 1 drug in the formulary with `unit_price_tzs > 0` and at least one stock
  batch with quantity > 5
- 1 service item for consultation (`CONS-NEW` etc.) — auto-charge falls back
  to hardcoded prices if missing, but seeding it is more realistic

**Browser**: open two browsers (e.g. Chrome + Firefox) or two browser
profiles so you can act as two roles at the same time. Notifications need
this.

---

## Section A — Auth + role-based UI

### A1. Each role sees only their menu

Log in as each role and tick off what's visible in the **top navigation**:

| Role | Should see | Should NOT see |
|---|---|---|
| ADMIN / superuser | Everything | — |
| RECEPTIONIST | Dashboard, Patients, Appointments, Billing, Reports | Pharmacy, Lab, Encounters |
| NURSE | Dashboard, Patients, Appointments | Pharmacy, Lab, Billing, Reports |
| CLINICIAN | Dashboard, Patients, Appointments, Pharmacy, Lab, Reports | Billing |
| PHARMACY | Dashboard, Patients, Pharmacy, Lab | Appointments, Billing, Encounters, Reports |
| LAB | Dashboard, Patients, Lab | Pharmacy, Billing, Appointments, Encounters, Reports |
| COUNSELLOR | Dashboard, Patients, Appointments, Reports | Pharmacy, Lab, Billing |

- [ ] Confirm each row above. Note any role that sees something it shouldn't.

### A2. Role-based dashboard

- [ ] Log in as **LAB** → should land directly on `/lab/queue/` (not the dashboard).
- [ ] Log in as **PHARMACY** → lands on `/pharmacy/queue/`.
- [ ] Log in as **RECEPTIONIST** or **NURSE** → lands on `/appointments/queue/`.
- [ ] Log in as **CLINICIAN** → lands on the dashboard. Confirm cards visible:
  Patients seen, Rx dispensed, Pending labs, Patient volume chart, Top
  diagnoses, Low stock, Near-expiry. Should NOT see Revenue.
- [ ] Log in as **ADMIN** → all 8 dashboard cards visible.

### A3. Language toggle

- [ ] Log in. Click the language flag in the top-right. Switch to Swahili.
- [ ] Reload the page → still Swahili (cookie persisted).
- [ ] Open a new tab to any page → still Swahili.
- [ ] Confirm common labels translated: Dashboard → Dashibodi, Patients →
  Wagonjwa, Logout → Toka.
- [ ] Switch back to English → instant.

### A4. Forbidden URLs

- [ ] As **NURSE**, manually navigate to `/billing/invoices/` → should get a
  403 forbidden page (not a crash).
- [ ] As **PHARMACY**, navigate to `/billing/invoices/` → 403.
- [ ] As **RECEPTIONIST**, navigate to `/lab/queue/` → 403.

---

## Section B — Patient + appointment flow

### B1. Register a patient

- [ ] As **RECEPTIONIST**, register a new patient (name, sex, age, phone). Save.
- [ ] Confirm patient appears in the list.
- [ ] Search for them by partial name → match found.

### B2. Book a walk-in

- [ ] Click "Walk-In / Book". Search for the patient via the picker.
- [ ] Confirm picker returns rows you can click. Click one.
- [ ] Tick walk-in. Pick a clinician. Submit.
- [ ] Confirm appointment appears on `/appointments/queue/` as "Checked In".
- [ ] Note: clinician name shown (not "—").

### B3. Schedule a future appointment

- [ ] Repeat B2 but untick walk-in. Pick a future date/time. Submit.
- [ ] Confirm appointment shows on the queue with status SCHEDULED.

---

## Section C — Encounter + payment gate

This is the biggest area. Use **two browser windows**: one as **CLINICIAN**,
one as **RECEPTIONIST**.

### C1. Start encounter — auto-charge fires

- [ ] As CLINICIAN, click on a checked-in patient → "Anza Consult".
- [ ] Confirm encounter detail page loads.
- [ ] **Yellow "Awaiting payment confirmation" banner** should appear at the
  top with the consultation amount and a link to the invoice.
- [ ] Try to add vitals or diagnosis → should get a flash error
  "Awaiting payment confirmation".
- [ ] The "+ Prescribe" and "+ Order test" links should show as 🔒 Locked.

### C2. Receptionist receives notification + records payment

- [ ] In the RECEPTIONIST window, look at the **bell icon** in the top nav.
- [ ] Within ~30 seconds, expect a **toast pop-up** "New consultation to
  collect" sliding from the top-right (auto-dismisses in 5s).
- [ ] Bell badge count should increment.
- [ ] Click the bell → see the notification in the dropdown.
- [ ] Click it → goes to the invoice.
- [ ] Record a payment for the full balance. Save.

### C3. Clinician notified + gate unlocks

- [ ] Switch to CLINICIAN window. Within ~30s expect a toast
  "Patient paid — ready for consult" (green).
- [ ] Reload the encounter page → yellow banner gone.
- [ ] Vitals form, diagnosis form, prescribe link, order-test link all unlocked.

### C4. SOAP autosave

- [ ] Type into Subjective field. Wait 2s. Look for "Saved" indicator
  near the encounter header.
- [ ] Switch tabs / close the tab without explicitly saving.
- [ ] Reopen the patient chart. Click "Resume" on the open-draft banner.
- [ ] Confirm your typing is preserved.

### C5. Add vitals → edit → delete

- [ ] Add a row of vitals. Confirm it appears in the side panel.
- [ ] Click "Edit" on the vitals. Change one number. Save → updates inline.
- [ ] Click "Delete" → confirms, row disappears.

### C6. Add diagnosis → edit → primary toggle

- [ ] Add a diagnosis. Tick "Primary".
- [ ] Edit it. Save.
- [ ] Add a second non-primary diagnosis. Confirm star icon shows on primary one.

### C7. Prescribe — auto-bill triggers payment gate again

- [ ] Click "+ Prescribe". Pick a drug, enter dose/frequency/qty/duration. Save.
- [ ] Check the encounter detail: a new prescription appears.
- [ ] Open the invoice in a new tab → should now have a NEW LINE for the drug
  (qty × unit price). Total increased. Status flipped to PARTIALLY_PAID.
- [ ] Encounter banner should re-lock with the new balance.
- [ ] RECEPTIONIST should get a toast "Drugs to collect payment for".

### C8. Pharmacy is locked until paid

- [ ] As PHARMACY, go to `/pharmacy/queue/` → see the new prescription.
- [ ] Click "Dispense". Should get a **flash error** "Awaiting payment
  confirmation. Patient owes X TZS — send them to the front desk".
- [ ] Bounced back to the queue.

### C9. Reception pays drug bill → pharmacy unlocks

- [ ] RECEPTIONIST records the second payment.
- [ ] PHARMACY navigates to dispense page → form now shows.
- [ ] Pick batch, dispense. Save.
- [ ] CLINICIAN gets toast "Prescription dispensed".
- [ ] Stock count for that drug decreases by the dispensed quantity.

### C10. Order a lab test

- [ ] CLINICIAN clicks "+ Order test" on the encounter (assuming consultation
  is paid). Pick a test. Save.
- [ ] LAB user gets toast "New lab order".
- [ ] LAB queue shows the new order with **amber row** + animated dot
  ("Awaiting collection").

### C11. Lab workflow

- [ ] As LAB, click "Mark collected" on the queue row → status updates inline.
  Row turns blue ("Sample collected — enter result").
- [ ] Click "Open" → order detail. Workflow stepper shows ✓ Ordered, ✓ Collected.
- [ ] Enter a numeric result that is in the reference range. Save.
- [ ] CLINICIAN gets toast "Lab result ready" (info, blue).
- [ ] Order disappears from `/lab/queue/`, appears on `/lab/results/`.

### C12. Critical lab result

- [ ] Order a second test. Mark collected.
- [ ] Enter a result that's <75% of the reference min OR >150% of the max.
- [ ] CLINICIAN gets a **CRITICAL toast** (red, stays for 12s).
- [ ] Bell badge shows the unread item.

### C13. Clinician reviews + amends

- [ ] CLINICIAN opens the resulted lab → "Mark reviewed".
- [ ] Confirm stepper now shows all 4 ✓.
- [ ] Click "Amend result" → enter a reason. Submit.
- [ ] Confirm the order reopens to RESULTED. Lab can re-enter the value.

### C14. Finalise the encounter

- [ ] CLINICIAN clicks "Finalise". Confirm.
- [ ] Encounter status → FINALISED. SOAP fields go read-only. Edit/delete
  buttons on vitals/diagnosis disappear.
- [ ] Click "Amend" → popover asks for a reason. Submit.
- [ ] Status flips to AMENDED. Fields editable again.

---

## Section D — Pharmacy

### D1. Add a drug with first batch

- [ ] As PHARMACY, go to drug catalogue → "Add drug".
- [ ] Fill drug fields. **Tick "Register first stock batch now"**. Fill batch
  number, expiry, qty.
- [ ] Save. Confirm both drug and batch were created.

### D2. Edit batch (fix typo)

- [ ] Open the drug detail. Click "Edit batch" on the batch row.
- [ ] Change the expiry date or batch number. Save.
- [ ] Confirm change persists.

### D3. Stock movement audit trail

- [ ] In Django admin, look at StockMovement entries → confirm the receive
  and any adjustments are recorded with the user who did them.

### D4. Pharmacy → lab read access

- [ ] As PHARMACY, click "Lab" in the top nav.
- [ ] Should see queue and results. Click into a resulted order.
- [ ] Confirm: result value visible, but **no edit / collect / review / amend
  buttons**. PHARMACY is read-only here.

---

## Section E — Lab reports

### E1. Period selector

- [ ] As LAB, click "Reports" in the lab sub-nav.
- [ ] Click each pill: This week / Last 2 weeks / Last 30 days.
- [ ] Volume cards, TAT, flags, top tests should all update for the new window.

### E2. Sanity-check numbers

- [ ] Total orders in the volume strip should equal sum of the four sub-buckets.
- [ ] Top tests should be in descending order by count.
- [ ] TAT should be in hours, > 0 if any results were entered.

---

## Section F — Edits + amendments

### F1. Cancel an undispensed Rx

- [ ] CLINICIAN writes an Rx, then clicks "Cancel" inline. Enter a reason. Confirm.
- [ ] Rx status → CANCELLED. Reason logged.

### F2. Void a dispensed Rx

- [ ] On a fully-dispensed prescription (from C9), as PHARMACY navigate to the
  patient chart's Prescriptions tab.
- [ ] Click "Void" on the dispensed row. Enter a reason. Confirm.
- [ ] Confirm: stock for that drug **increased** back by the dispensed quantity.
  Rx status → CANCELLED.

### F3. Void a payment

- [ ] As RECEPTIONIST or ADMIN, open an invoice with at least one payment.
- [ ] Click "Void" on a payment. Enter a reason. Confirm.
- [ ] Confirm: invoice has a new negative-amount payment row (red), original
  payment still visible (green). Balance restored.

---

## Section G — Notifications

### G1. Bell auto-poll

- [ ] Sit on any page logged in. Have another user generate an event for you
  (e.g. RECEPTIONIST records a payment for your encounter).
- [ ] Within 30 seconds: bell badge count increments + toast pops.

### G2. Mark all read

- [ ] Click the bell. If unread, click "Mark all read".
- [ ] Confirm badge clears, dropdown shows all entries un-highlighted.

### G3. Don't notify yourself

- [ ] As LAB, enter a result yourself.
- [ ] Confirm you do NOT get a "Lab result ready" notification — that goes to
  the ordering clinician, not the actor.

---

## Section H — Offline / weird states

### H1. Service worker self-heal (if a user reports "only works in incognito")

- [ ] Open browser dev tools → Application → Service Workers. Confirm
  registration on `/sw.js` is active.
- [ ] In the network tab, simulate offline → static assets still load (cached).
- [ ] Navigation while offline shows the offline banner; actions queue or fail
  cleanly (depends on your tolerance — currently they fail with a CSRF or
  network error, which is expected).

### H2. Idle session timeout

- [ ] Log in. Wait > 15 minutes without interacting (default
  `IDLE_SESSION_SECONDS=900`).
- [ ] Try to perform any action → redirected to login with "You were signed
  out due to inactivity".

---

## What "passing" looks like

- All sections A–G complete with no blocking failures.
- Section H is best-effort; offline behavior is "graceful degradation",
  not "everything works offline".
- File any failure with: which step (e.g. C7), what role, what you saw,
  what you expected.

## Known limitations (won't be tested here, by design)

- Concurrent writes to the same record (two clinicians on one encounter,
  two pharmacy users dispensing same Rx). The DB has `select_for_update`
  on the critical paths but no UI feedback for conflicts.
- Web push (OS-level notifications) — not implemented; bell + toast only.
- SMS notifications — not implemented.
- Patient self-service portal — not in scope.

## After testing

If green: ship. If red: file a list of failures and we triage by severity
(payment gate broken = stop ship; cosmetic CSS = next sprint).
