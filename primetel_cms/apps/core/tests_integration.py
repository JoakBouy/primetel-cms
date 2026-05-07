"""End-to-end integration tests for the recent feature work:

- Role-based nav visibility
- Hard payment gate (consultation, prescriptions)
- Notification fan-out + bell pop-up payload
- Auto-bill on prescribe
- Lab workflow + reports
- Pharmacy first-batch creation + edit
- Billing void payment
- Encounter amend-with-reason

These run as part of the full pytest suite. They are intentionally
end-to-end (HTTP-level via the test Client) so a regression in any of the
glue code surfaces here.
"""
import calendar
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.billing.models import Invoice, InvoiceLine, Payment, ServiceItem
from apps.core.context_processors import _nav_visibility
from apps.core.models import Notification
from apps.core.notifications import notify_user
from apps.encounters.models import Encounter
from apps.lab.models import LabOrder, LabResult, LabTest
from apps.patients.models import Patient
from apps.pharmacy.models import Drug, StockItem


User = get_user_model()


# ──────────────────────────────────────────────────────────────────
#  Shared fixtures
# ──────────────────────────────────────────────────────────────────

@pytest.fixture
def role_admin(db):
    return Role.objects.create(code="ADMIN", display_name="Admin")


@pytest.fixture
def role_clinician(db):
    return Role.objects.create(code="CLINICIAN", display_name="Clinician")


@pytest.fixture
def role_nurse(db):
    return Role.objects.create(code="NURSE", display_name="Nurse")


@pytest.fixture
def role_recep(db):
    return Role.objects.create(code="RECEPTIONIST", display_name="Receptionist")


@pytest.fixture
def role_pharm(db):
    return Role.objects.create(code="PHARMACY", display_name="Pharmacy")


@pytest.fixture
def role_lab(db):
    return Role.objects.create(code="LAB", display_name="Lab")


@pytest.fixture
def role_finance(db):
    return Role.objects.create(code="FINANCE", display_name="Finance")


@pytest.fixture
def role_counsellor(db):
    return Role.objects.create(code="COUNSELLOR", display_name="Counsellor")


@pytest.fixture
def admin_user(db, role_admin):
    return User.objects.create_user(username="admin1", password="pw", role=role_admin)


@pytest.fixture
def clinician(db, role_clinician):
    return User.objects.create_user(
        username="clin1", password="pw", role=role_clinician, full_name="Dr. Clinician"
    )


@pytest.fixture
def nurse(db, role_nurse):
    return User.objects.create_user(username="nurse1", password="pw", role=role_nurse)


@pytest.fixture
def receptionist(db, role_recep):
    return User.objects.create_user(username="recep1", password="pw", role=role_recep)


@pytest.fixture
def pharmacist(db, role_pharm):
    return User.objects.create_user(username="pharm1", password="pw", role=role_pharm)


@pytest.fixture
def lab_tech(db, role_lab):
    return User.objects.create_user(username="lab1", password="pw", role=role_lab)


@pytest.fixture
def patient(db):
    return Patient.objects.create(
        full_name="Asha Mwangi",
        sex="F",
        date_of_birth=date(1990, 5, 1),
        phone="+255712345678",
    )


@pytest.fixture
def encounter(db, patient, clinician):
    """A draft encounter with chief_complaint AND assessment so it can be
    finalised in tests that need that. SOAP fields are otherwise blank."""
    return Encounter.objects.create(
        patient=patient,
        clinician=clinician,
        encounter_type="GENERAL",
        chief_complaint="Headache",
        assessment="Tension headache",
    )


@pytest.fixture
def consult_invoice(db, encounter, receptionist):
    """Open consultation invoice with a 5000 TZS line. Default unpaid."""
    inv = Invoice.objects.create(
        patient=encounter.patient,
        encounter=encounter,
        issued_by=receptionist,
        created_by=receptionist,
    )
    InvoiceLine.objects.create(
        invoice=inv,
        description="Consultation",
        quantity=Decimal("1"),
        unit_price_tzs=Decimal("5000"),
    )
    inv.recalculate()
    inv.status = "ISSUED"
    inv.save(update_fields=["status"])
    return inv


@pytest.fixture
def drug(db):
    return Drug.objects.create(
        generic_name="Paracetamol",
        strength="500mg",
        form="TABLET",
        unit_price_tzs=Decimal("200"),
    )


@pytest.fixture
def stock_batch(db, drug):
    return StockItem.objects.create(
        drug=drug,
        batch_number="PCM-2026-01",
        expiry_date=date.today() + timedelta(days=365),
        quantity_on_hand=100,
    )


# ──────────────────────────────────────────────────────────────────
#  Role-based nav visibility
# ──────────────────────────────────────────────────────────────────

ROLE_EXPECTATIONS = {
    "ADMIN":        {"patients", "appointments", "encounters", "pharmacy", "lab", "billing", "reports"},
    "RECEPTIONIST": {"patients", "appointments", "billing", "reports"},
    "NURSE":        {"patients", "appointments", "encounters"},
    "CLINICIAN":    {"patients", "appointments", "encounters", "pharmacy", "lab", "reports"},
    "COUNSELLOR":   {"patients", "appointments", "encounters", "reports"},
    "PHARMACY":     {"pharmacy", "patients", "lab"},
    "LAB":          {"lab", "patients"},
    "FINANCE":      {"billing", "reports", "patients"},
}


@pytest.mark.django_db
@pytest.mark.parametrize("code,expected", list(ROLE_EXPECTATIONS.items()))
def test_nav_visibility_per_role(code, expected):
    role = Role.objects.create(code=code, display_name=code.title())
    user = User.objects.create_user(username=f"u_{code.lower()}", password="pw", role=role)
    nav = _nav_visibility(user)
    visible = {k for k, v in nav.items() if v}
    assert visible == expected, f"{code} got {visible}, expected {expected}"


@pytest.mark.django_db
def test_nav_visibility_anonymous_sees_nothing():
    nav = _nav_visibility(None)
    assert all(v is False for v in nav.values())


@pytest.mark.django_db
def test_nav_visibility_superuser_sees_everything():
    su = User.objects.create_superuser(username="su", password="pw", email="su@x.com")
    nav = _nav_visibility(su)
    assert all(v is True for v in nav.values())


# ──────────────────────────────────────────────────────────────────
#  Forbidden URLs raise 403, not 500
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_nurse_cannot_open_billing(client, nurse):
    client.force_login(nurse)
    resp = client.get(reverse("billing:invoice_list"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_pharmacy_cannot_open_billing(client, pharmacist):
    client.force_login(pharmacist)
    resp = client.get(reverse("billing:invoice_list"))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_receptionist_cannot_order_lab(client, receptionist, encounter):
    client.force_login(receptionist)
    resp = client.get(reverse("lab:order_new", args=[encounter.pk]))
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────
#  Receptionist == finance: can record + void payments
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_receptionist_can_record_payment(client, receptionist, consult_invoice):
    client.force_login(receptionist)
    resp = client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    assert resp.status_code == 302
    consult_invoice.refresh_from_db()
    assert consult_invoice.status == "PAID"
    assert consult_invoice.balance_tzs == 0


@pytest.mark.django_db
def test_receptionist_can_void_payment(client, receptionist, consult_invoice):
    """The void-payment endpoint used to be FINANCE-only. After the merge,
    receptionists can void too."""
    client.force_login(receptionist)
    # Record then void
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    pay = Payment.objects.filter(invoice=consult_invoice, amount_tzs=5000).first()
    assert pay is not None
    resp = client.post(
        reverse("billing:payment_void", args=[consult_invoice.pk, pay.pk]),
        data={"reason": "Wrong patient"},
    )
    assert resp.status_code == 302
    # A reversing payment exists.
    assert Payment.objects.filter(invoice=consult_invoice, amount_tzs=-5000).exists()


# ──────────────────────────────────────────────────────────────────
#  Hard payment gate
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_payment_gate_blocks_vitals_when_unpaid(client, nurse, encounter, consult_invoice):
    client.force_login(nurse)
    resp = client.post(
        reverse("encounters:add_vitals", args=[encounter.pk]),
        data={"pulse": 80},
    )
    # View redirects with a flash error instead of creating vitals.
    assert resp.status_code == 302
    assert encounter.vitals.count() == 0


@pytest.mark.django_db
def test_payment_gate_blocks_diagnosis_when_unpaid(client, clinician, encounter, consult_invoice):
    client.force_login(clinician)
    resp = client.post(
        reverse("encounters:add_diagnosis", args=[encounter.pk]),
        data={"description": "Tension headache"},
    )
    assert resp.status_code == 302
    assert encounter.diagnoses.count() == 0


@pytest.mark.django_db
def test_payment_gate_unlocks_after_payment(client, nurse, receptionist, encounter, consult_invoice):
    """End-to-end: reception pays → clinical writes succeed."""
    # Pay it off as reception.
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    consult_invoice.refresh_from_db()
    assert consult_invoice.status == "PAID"

    # Now the nurse can record vitals.
    client.force_login(nurse)
    client.post(
        reverse("encounters:add_vitals", args=[encounter.pk]),
        data={"pulse": 75, "temperature": "36.8"},
    )
    assert encounter.vitals.count() == 1


@pytest.mark.django_db
def test_clinician_can_record_vitals_after_payment(client, clinician, receptionist, encounter, consult_invoice):
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )

    client.force_login(clinician)
    resp = client.post(
        reverse("encounters:add_vitals", args=[encounter.pk]),
        data={"pulse": 82, "temperature": "37.1"},
    )
    assert resp.status_code == 302
    vitals = encounter.vitals.get()
    assert vitals.recorded_by == clinician
    assert vitals.pulse == 82


@pytest.mark.django_db
def test_lab_order_allowed_unpaid_bills_and_notifies_reception(
    client, clinician, lab_tech, receptionist, encounter, consult_invoice
):
    client.force_login(clinician)
    test = LabTest.objects.create(
        code="HGB", name="Haemoglobin", specimen_type="BLOOD", price_tzs=Decimal("3000")
    )
    Notification.objects.all().delete()

    resp = client.post(reverse("lab:order_new", args=[encounter.pk]), data={"test": str(test.pk)})
    assert resp.status_code == 302
    order = LabOrder.objects.get(test=test, encounter=encounter)
    consult_invoice.refresh_from_db()
    assert consult_invoice.lines.count() == 2
    assert consult_invoice.total_tzs == Decimal("8000")
    assert consult_invoice.balance_tzs == Decimal("8000")
    assert Notification.objects.filter(recipient=lab_tech, title="New lab order").exists()
    assert Notification.objects.filter(recipient=receptionist, kind="PAYMENT_REQUIRED").exists()

    client.force_login(lab_tech)
    queue = client.get(reverse("lab:queue"))
    assert queue.status_code == 200
    assert str(order.pk).encode() in queue.content
    assert b"Payment due" in queue.content


@pytest.mark.django_db
def test_lab_collect_blocked_until_invoice_paid(client, clinician, lab_tech, receptionist, encounter, consult_invoice):
    test = LabTest.objects.create(
        code="HGB", name="Haemoglobin", specimen_type="BLOOD", price_tzs=Decimal("3000")
    )
    client.force_login(clinician)
    client.post(reverse("lab:order_new", args=[encounter.pk]), data={"test": str(test.pk)})
    order = LabOrder.objects.get(test=test, encounter=encounter)

    client.force_login(lab_tech)
    resp = client.post(reverse("lab:collect", args=[order.pk]))
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.status == "ORDERED"

    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "8000"},
    )
    consult_invoice.refresh_from_db()
    assert consult_invoice.status == "PAID"

    client.force_login(lab_tech)
    resp = client.post(reverse("lab:collect", args=[order.pk]))
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.status == "COLLECTED"


@pytest.mark.django_db
def test_payment_gate_blocks_prescribe_when_unpaid(client, clinician, encounter, consult_invoice, drug):
    client.force_login(clinician)
    resp = client.get(reverse("pharmacy:rx_prescribe", args=[encounter.pk]))
    assert resp.status_code == 302
    assert reverse("encounters:detail", args=[encounter.pk]) in resp.headers.get("Location", "")


# ──────────────────────────────────────────────────────────────────
#  Auto-bill on prescribe
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_prescribe_auto_bills_invoice(client, clinician, receptionist, encounter, consult_invoice, drug, stock_batch):
    """Writing an Rx for a drug with unit_price > 0 must add a billing line."""
    # Pay the consultation first so the gate doesn't block.
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )

    # Clinician prescribes 10 paracetamol at 200 TZS each = 2000 TZS additional.
    client.force_login(clinician)
    resp = client.post(
        reverse("pharmacy:rx_prescribe", args=[encounter.pk]),
        data={
            "drug": str(drug.pk),
            "dose": "1 tab",
            "frequency": "BID",
            "duration_days": "5",
            "quantity": "10",
        },
    )
    assert resp.status_code == 302
    consult_invoice.refresh_from_db()
    # New billing line was added at qty * unit_price.
    assert consult_invoice.lines.count() == 2
    assert consult_invoice.total_tzs == Decimal("7000")
    # Patient now owes again — invoice flipped from PAID to PARTIALLY_PAID.
    assert consult_invoice.status == "PARTIALLY_PAID"
    assert consult_invoice.balance_tzs == Decimal("2000")


@pytest.mark.django_db
def test_dispense_blocked_when_unpaid(client, pharmacist, clinician, encounter, consult_invoice, drug, stock_batch, receptionist):
    """After Rx is added, the invoice has new balance — pharmacy can't dispense
    until reception takes that payment too."""
    # Pay consult, prescribe (which adds an unpaid line), don't pay it.
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    client.force_login(clinician)
    client.post(
        reverse("pharmacy:rx_prescribe", args=[encounter.pk]),
        data={"drug": str(drug.pk), "dose": "1", "frequency": "BID",
              "duration_days": "5", "quantity": "10"},
    )
    rx = encounter.prescriptions.first()
    assert rx is not None

    # Pharmacy tries to dispense — should bounce back to queue with error.
    client.force_login(pharmacist)
    resp = client.get(reverse("pharmacy:rx_dispense", args=[rx.pk]))
    assert resp.status_code == 302
    assert reverse("pharmacy:rx_queue") in resp.headers.get("Location", "")


# ──────────────────────────────────────────────────────────────────
#  Notifications
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_payment_clears_notifies_clinician(client, clinician, receptionist, encounter, consult_invoice):
    """When the consultation invoice is fully paid, the encounter's clinician
    should get a 'patient paid — ready for consult' notification."""
    Notification.objects.filter(recipient=clinician).delete()

    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )

    notifs = Notification.objects.filter(recipient=clinician, kind="PAYMENT_RECEIVED")
    assert notifs.count() == 1
    n = notifs.first()
    assert n.level == "SUCCESS"
    assert "Asha Mwangi" in n.body or encounter.patient.full_name in n.body


@pytest.mark.django_db
def test_lab_order_notifies_lab_role(client, clinician, lab_tech, receptionist, encounter, consult_invoice):
    """A lab order placed by a clinician notifies all LAB users (not the clinician)."""
    LabTest.objects.create(code="HGB", name="Haemoglobin", specimen_type="BLOOD")
    test = LabTest.objects.get(code="HGB")
    # Pay so gate doesn't block.
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    Notification.objects.all().delete()

    client.force_login(clinician)
    client.post(reverse("lab:order_new", args=[encounter.pk]), data={"test": str(test.pk)})

    # Lab tech got notified, clinician (the actor) did not.
    assert Notification.objects.filter(recipient=lab_tech).count() == 1
    assert Notification.objects.filter(recipient=clinician).count() == 0


@pytest.mark.django_db
def test_critical_lab_result_notifies_clinician_critical_level(
    client, clinician, lab_tech, receptionist, encounter, consult_invoice
):
    """A CRITICAL flag should result in a CRITICAL-level notification."""
    test = LabTest.objects.create(
        code="HGB", name="Haemoglobin", specimen_type="BLOOD",
        reference_range_min=Decimal("12.0"), reference_range_max=Decimal("16.0"),
    )
    # Pay, order, collect.
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    client.force_login(clinician)
    client.post(reverse("lab:order_new", args=[encounter.pk]), data={"test": str(test.pk)})
    order = LabOrder.objects.get(test=test, encounter=encounter)
    client.force_login(lab_tech)
    client.post(reverse("lab:collect", args=[order.pk]))

    Notification.objects.filter(recipient=clinician).delete()
    # Enter a critically low result: 4.0 is < 75% of 12.0 (= 9.0).
    client.post(
        reverse("lab:result_enter", args=[order.pk]),
        data={"value_numeric": "4.0"},
    )

    notif = Notification.objects.filter(recipient=clinician).first()
    assert notif is not None
    assert notif.kind == "LAB_CRITICAL"
    assert notif.level == "CRITICAL"


@pytest.mark.django_db
def test_self_action_does_not_notify_actor(clinician):
    """The fan-out helper must exclude the actor from role-wide broadcasts."""
    from apps.core.notifications import notify_role
    Notification.objects.filter(recipient=clinician).delete()
    notify_role(
        "CLINICIAN", exclude_actor=clinician,
        kind="INFO", title="Test", body="Should not see this", url="/",
    )
    assert Notification.objects.filter(recipient=clinician).count() == 0


# ──────────────────────────────────────────────────────────────────
#  Bell badge endpoint with HX-Trigger pop-ups
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_bell_first_poll_does_not_pop_inbox(client, clinician):
    """The very first poll of a session shouldn't pop pre-existing
    notifications as toasts. Only fresh ones do."""
    # Pre-existing unread notification.
    notify_user(
        clinician, kind="INFO", level="INFO",
        title="Pre-existing", body="x", url="/",
    )
    client.force_login(clinician)
    resp = client.get(reverse("notifications_badge"))
    assert resp.status_code == 200
    # No HX-Trigger because this is the first poll.
    assert "HX-Trigger" not in resp.headers


@pytest.mark.django_db
def test_bell_subsequent_poll_emits_hx_trigger_for_new_notification(client, clinician):
    client.force_login(clinician)
    # Prime the session marker with a first poll.
    client.get(reverse("notifications_badge"))

    # New notification arrives.
    notify_user(
        clinician, kind="LAB_CRITICAL", level="CRITICAL",
        title="Critical Hb", body="Asha Mwangi · 4.2", url="/lab/orders/x/",
    )

    resp = client.get(reverse("notifications_badge"))
    assert "HX-Trigger" in resp.headers
    payload = resp.headers["HX-Trigger"]
    assert "notify-toast" in payload
    assert "Critical Hb" in payload
    assert "critical" in payload  # lower-cased level


@pytest.mark.django_db
def test_bell_mark_all_read(client, clinician):
    notify_user(clinician, kind="INFO", title="One", url="/")
    notify_user(clinician, kind="INFO", title="Two", url="/")
    assert Notification.objects.filter(recipient=clinician, is_read=False).count() == 2

    client.force_login(clinician)
    client.post(reverse("notifications_mark_all_read"))
    assert Notification.objects.filter(recipient=clinician, is_read=False).count() == 0


# ──────────────────────────────────────────────────────────────────
#  Pharmacy: drug-with-batch creation, stock edit
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_drug_create_with_first_batch(client, pharmacist):
    client.force_login(pharmacist)
    expiry_candidate = date.today() + timedelta(days=400)
    expiry = expiry_candidate.strftime("%Y-%m")
    resp = client.post(
        reverse("pharmacy:drug_create"),
        data={
            "generic_name": "Amoxicillin",
            "strength": "500mg",
            "form": "CAPSULE",
            "pack_size": "30",
            "unit_price_tzs": "300",
            "low_stock_threshold": "20",
            "is_active": "on",
            "batch_number": "AMX-2026-04",
            "expiry_date": expiry,
            "quantity_on_hand": "100",
        },
    )
    assert resp.status_code == 302
    drug = Drug.objects.get(generic_name="Amoxicillin")
    batch = drug.stock_items.first()
    assert batch is not None
    assert batch.quantity_on_hand == 100
    assert batch.batch_number == "AMX-2026-04"
    assert batch.expiry_date == date(
        expiry_candidate.year,
        expiry_candidate.month,
        calendar.monthrange(expiry_candidate.year, expiry_candidate.month)[1],
    )


@pytest.mark.django_db
def test_drug_create_without_batch(client, pharmacist):
    """Adding a drug without filling the optional batch fields should still work."""
    client.force_login(pharmacist)
    resp = client.post(
        reverse("pharmacy:drug_create"),
        data={
            "generic_name": "Ibuprofen",
            "strength": "200mg",
            "form": "TABLET",
            "pack_size": "20",
            "unit_price_tzs": "100",
            "low_stock_threshold": "10",
            "is_active": "on",
        },
    )
    assert resp.status_code == 302
    drug = Drug.objects.get(generic_name="Ibuprofen")
    assert drug.stock_items.count() == 0


@pytest.mark.django_db
def test_drug_create_with_partial_batch_fields_rejected(client, pharmacist):
    """If the user fills batch_number but leaves expiry blank, we must reject
    with a clear error rather than silently dropping the data."""
    client.force_login(pharmacist)
    initial = Drug.objects.count()
    resp = client.post(
        reverse("pharmacy:drug_create"),
        data={
            "generic_name": "Foo",
            "strength": "100mg",
            "form": "TABLET",
            "pack_size": "1",
            "unit_price_tzs": "100",
            "low_stock_threshold": "5",
            "is_active": "on",
            "batch_number": "F-001",  # filled
            # expiry_date missing
            # quantity_on_hand missing
        },
    )
    # The view re-renders the form (status 200) with an error message and the
    # drug is NOT saved.
    assert resp.status_code == 200
    assert Drug.objects.count() == initial


@pytest.mark.django_db
def test_stock_edit_updates_batch(client, pharmacist, drug, stock_batch):
    client.force_login(pharmacist)
    expiry_candidate = date.today() + timedelta(days=200)
    new_expiry = expiry_candidate.strftime("%Y-%m")
    resp = client.post(
        reverse("pharmacy:stock_edit", args=[stock_batch.pk]),
        data={
            "batch_number": "PCM-CORRECTED",
            "expiry_date": new_expiry,
            "quantity_on_hand": "80",
        },
    )
    assert resp.status_code == 302
    stock_batch.refresh_from_db()
    assert stock_batch.batch_number == "PCM-CORRECTED"
    assert stock_batch.quantity_on_hand == 80
    assert stock_batch.expiry_date == date(
        expiry_candidate.year,
        expiry_candidate.month,
        calendar.monthrange(expiry_candidate.year, expiry_candidate.month)[1],
    )


# ──────────────────────────────────────────────────────────────────
#  Pharmacy can read lab; cannot enter results
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_pharmacy_can_view_lab_queue(client, pharmacist):
    client.force_login(pharmacist)
    resp = client.get(reverse("lab:queue"))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_pharmacy_cannot_enter_lab_results(client, pharmacist, clinician, lab_tech, encounter, consult_invoice, receptionist):
    """PHARMACY has read-only access to lab — they must not be able to POST
    a result."""
    test = LabTest.objects.create(code="HGB", name="Haemoglobin", specimen_type="BLOOD")
    # Pay + order + collect (need a real ORDERED→COLLECTED order).
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    client.force_login(clinician)
    client.post(reverse("lab:order_new", args=[encounter.pk]), data={"test": str(test.pk)})
    order = LabOrder.objects.get(test=test, encounter=encounter)
    client.force_login(lab_tech)
    client.post(reverse("lab:collect", args=[order.pk]))

    # Pharmacist tries to write a result. Should 403.
    client.force_login(pharmacist)
    resp = client.post(
        reverse("lab:result_enter", args=[order.pk]),
        data={"value_numeric": "10"},
    )
    assert resp.status_code == 403


# ──────────────────────────────────────────────────────────────────
#  Lab reports
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_lab_reports_renders_for_each_period(client, lab_tech):
    client.force_login(lab_tech)
    for period in ["week", "biweek", "month"]:
        resp = client.get(reverse("lab:reports") + f"?period={period}")
        assert resp.status_code == 200, f"period={period} returned {resp.status_code}"
        assert resp.context["selected_period"] == period


@pytest.mark.django_db
def test_lab_reports_volume_counts(
    client, lab_tech, clinician, receptionist, encounter, consult_invoice
):
    """Place 3 orders, result 1, cancel 0. Reports should show volume = 3."""
    test = LabTest.objects.create(code="HGB", name="Haemoglobin", specimen_type="BLOOD")
    client.force_login(receptionist)
    client.post(
        reverse("billing:payment_record", args=[consult_invoice.pk]),
        data={"method": "CASH", "amount_tzs": "5000"},
    )
    client.force_login(clinician)
    for _ in range(3):
        client.post(reverse("lab:order_new", args=[encounter.pk]), data={"test": str(test.pk)})
    assert LabOrder.objects.count() == 3

    client.force_login(lab_tech)
    resp = client.get(reverse("lab:reports") + "?period=week")
    assert resp.status_code == 200
    assert resp.context["volume_total"] == 3


# ──────────────────────────────────────────────────────────────────
#  Encounter amend-with-reason
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_amend_finalised_encounter_requires_reason(client, clinician, encounter):
    encounter.finalise(user=clinician)
    encounter.refresh_from_db()
    assert encounter.status == "FINALISED"

    client.force_login(clinician)
    # Submit without a reason — should NOT amend.
    resp = client.post(reverse("encounters:amend", args=[encounter.pk]), data={})
    assert resp.status_code == 302
    encounter.refresh_from_db()
    assert encounter.status == "FINALISED", "Amend without reason should be rejected"

    # Submit with reason — should amend.
    resp2 = client.post(
        reverse("encounters:amend", args=[encounter.pk]),
        data={"reason": "Corrected diagnosis after lab"},
    )
    assert resp2.status_code == 302
    encounter.refresh_from_db()
    assert encounter.status == "AMENDED"


# ──────────────────────────────────────────────────────────────────
#  Translation toggle (basic round-trip)
# ──────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_swahili_translations_compiled():
    """Sanity check: the .mo file exists and Django reads it."""
    from django.utils import translation
    with translation.override("sw"):
        from django.utils.translation import gettext as _
        assert _("Patients") == "Wagonjwa"
        assert _("Sample collected") == "Sampuli imechukuliwa"
        assert _("Awaiting collection") == "Inasubiri kuchukuliwa"
