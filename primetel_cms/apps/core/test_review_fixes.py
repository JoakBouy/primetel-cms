"""Regression tests for the issues found in the codebase review.

Each test names the problem it guards against so a failure points straight
at the behaviour that regressed.
"""
import time
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.billing.consultation import auto_charge, prepay_consultation
from apps.billing.models import Invoice, InvoiceLine, Payment, ServiceItem
from apps.core.models import AuditLog, ReasonCode
from apps.core.utils import get_client_ip, strip_language_prefix
from apps.encounters.models import Encounter, EncounterAttachment, Vitals
from apps.lab.models import LabOrder, LabResult, LabTest
from apps.patients.models import Patient
from apps.pharmacy.allergy import check_allergy
from apps.pharmacy.models import Dispense, Drug, Prescription, StockItem, StockMovement

User = get_user_model()

# A valid 1x1 PNG.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ─── Fixtures ────────────────────────────────────────────────────

def _user(code, username, **extra):
    role, _ = Role.objects.get_or_create(code=code, defaults={"display_name": code.title()})
    return User.objects.create_user(username=username, password="pw-Secret-123", role=role, full_name=username, **extra)


@pytest.fixture
def admin_user(db):
    return _user("ADMIN", "admin1")


@pytest.fixture
def clinician(db):
    return _user("CLINICIAN", "clin1")


@pytest.fixture
def other_clinician(db):
    return _user("CLINICIAN", "clin2")


@pytest.fixture
def nurse(db):
    return _user("NURSE", "nurse1")


@pytest.fixture
def receptionist(db):
    return _user("RECEPTIONIST", "recep1")


@pytest.fixture
def pharmacist(db):
    return _user("PHARMACY", "pharm1")


@pytest.fixture
def lab_tech(db):
    return _user("LAB", "lab1")


@pytest.fixture
def counsellor(db):
    return _user("COUNSELLOR", "couns1")


@pytest.fixture
def patient(db):
    return Patient.objects.create(full_name="Asha Mwangi", sex="F", date_of_birth=date(1990, 5, 1))


@pytest.fixture
def encounter(db, patient, clinician):
    return Encounter.objects.create(
        patient=patient, clinician=clinician, encounter_type="GENERAL",
        chief_complaint="Fever", assessment="Malaria",
    )


@pytest.fixture
def paid_invoice(db, encounter, receptionist):
    inv = Invoice.objects.create(patient=encounter.patient, encounter=encounter, issued_by=receptionist)
    InvoiceLine.objects.create(invoice=inv, description="Consultation", quantity=Decimal("1"), unit_price_tzs=Decimal("5000"))
    inv.status = "ISSUED"
    inv.recalculate()
    Payment.objects.create(invoice=inv, method="CASH", amount_tzs=Decimal("5000"), received_by=receptionist)
    inv.refresh_from_db()
    return inv


@pytest.fixture
def amoxicillin(db):
    return Drug.objects.create(generic_name="Amoxicillin", strength="500mg", form="CAPSULE", unit_price_tzs=Decimal("200"))


@pytest.fixture
def paracetamol(db):
    return Drug.objects.create(generic_name="Paracetamol", strength="500mg", form="TABLET", unit_price_tzs=Decimal("100"))


def _batch(drug, qty=100, days=365, number="B-1"):
    return StockItem.objects.create(
        drug=drug, batch_number=number, quantity_on_hand=qty,
        expiry_date=timezone.localdate() + timedelta(days=days),
    )


# ─── Sessions ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_background_poll_does_not_keep_session_alive(client, clinician, settings):
    """Issue: the bell poll refreshed the idle timer, so tabs never timed out."""
    settings.IDLE_SESSION_SECONDS = 60
    client.force_login(clinician)
    client.get(reverse("dashboard"))
    session = client.session
    stale = int(time.time()) - 50
    session["_last_activity"] = stale
    session.save()

    assert client.get(reverse("notifications_badge")).status_code == 200
    assert client.session["_last_activity"] == stale  # poll did not count as activity

    session = client.session
    session["_last_activity"] = int(time.time()) - 120
    session.save()
    resp = client.get(reverse("notifications_badge"), HTTP_HX_REQUEST="true")
    assert "/login/" in resp.headers.get("HX-Redirect", "")


@pytest.mark.django_db
def test_absolute_session_lifetime_enforced(client, clinician, settings):
    """Issue: SESSION_SAVE_EVERY_REQUEST made the 8-hour cap a sliding window."""
    client.force_login(clinician)
    client.get(reverse("dashboard"))
    session = client.session
    session["_session_started"] = int(time.time()) - settings.SESSION_COOKIE_AGE - 10
    session.save()
    resp = client.get(reverse("dashboard"))
    assert resp.status_code == 302 and "/login/" in resp["Location"]


# ─── Audit / IP ──────────────────────────────────────────────────

def test_strip_language_prefix():
    assert strip_language_prefix("/en/patients/abc/") == "/patients/abc/"
    assert strip_language_prefix("/sw/patients/") == "/patients/"
    assert strip_language_prefix("/patients/") == "/patients/"
    assert strip_language_prefix("/english/") == "/english/"
    assert strip_language_prefix("/en") == "/"


@pytest.mark.django_db
def test_chart_view_audited_for_english_urls(client, clinician, patient):
    """Issue: /en/patients/<id>/ was logged as a generic 'Request'."""
    client.force_login(clinician)
    client.get(f"/en/patients/{patient.pk}/")
    assert AuditLog.objects.filter(entity_type="Patient", entity_id=patient.pk, action="READ").exists()


def test_client_ip_ignores_spoofed_forwarded_for(settings):
    """Issue: the left-most X-Forwarded-For entry (client-controlled) was trusted."""
    rf = RequestFactory()
    request = rf.get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 41.59.1.10", REMOTE_ADDR="10.0.0.1")
    settings.TRUSTED_PROXY_COUNT = 1
    assert get_client_ip(request) == "41.59.1.10"
    settings.TRUSTED_PROXY_COUNT = 0
    assert get_client_ip(request) == "10.0.0.1"


@pytest.mark.django_db
def test_healthz_does_not_leak_exception(client, monkeypatch):
    from django.db import connection

    def broken_cursor(*args, **kwargs):
        raise RuntimeError("password authentication failed for user secret-db-user")

    monkeypatch.setattr(connection, "cursor", broken_cursor)
    resp = client.get("/healthz/")
    assert resp.status_code == 503
    assert b"secret-db-user" not in resp.content


# ─── Protected media ─────────────────────────────────────────────

@pytest.mark.django_db
def test_patient_photo_served_only_to_logged_in_staff(client, nurse, patient, settings, tmp_path):
    """Issue: /media/ wasn't served in production, and would have been public."""
    settings.MEDIA_ROOT = tmp_path
    patient.photo = SimpleUploadedFile("p.png", PNG_BYTES, content_type="image/png")
    patient.save()
    url = reverse("protected_media", args=[patient.photo.name])

    assert client.get(url).status_code == 302  # anonymous → login
    client.force_login(nurse)
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    assert client.get(reverse("protected_media", args=["patient_photos/not-referenced.png"])).status_code == 404


@pytest.mark.django_db
def test_mh_attachment_hidden_from_nurse(client, nurse, counsellor, patient, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    mh = Encounter.objects.create(patient=patient, clinician=counsellor, encounter_type="MENTAL_HEALTH")
    att = EncounterAttachment.objects.create(
        encounter=mh, label="Letter", uploaded_by=counsellor,
        file=SimpleUploadedFile("l.pdf", b"%PDF-1.4 test", content_type="application/pdf"),
    )
    url = reverse("protected_media", args=[att.file.name])
    client.force_login(nurse)
    assert client.get(url).status_code == 404
    client.force_login(counsellor)
    assert client.get(url).status_code == 200


# ─── Factory reset ───────────────────────────────────────────────

@pytest.mark.django_db
def test_factory_reset_disabled_unless_enabled(client, settings):
    settings.ALLOW_FACTORY_RESET = False
    su = User.objects.create_superuser(username="root", password="pw-Secret-123", email="r@x.io")
    client.force_login(su)
    assert client.get("/en/admin/system/factory-reset/").status_code == 403


@pytest.mark.django_db
def test_factory_reset_needs_password_and_keeps_audit_log(client, settings, patient):
    """Issue: reset wiped the audit log and wrote no record of itself."""
    settings.ALLOW_FACTORY_RESET = True
    su = User.objects.create_superuser(username="root", password="pw-Secret-123", email="r@x.io")
    AuditLog.objects.create(actor=su, action="READ", entity_type="Patient", entity_id=patient.pk)
    client.force_login(su)
    url = "/en/admin/system/factory-reset/"

    client.post(url, {"confirm_phrase": "WIPE", "password": "wrong"})
    assert Patient.objects.all_including_deleted().exists()

    client.post(url, {"confirm_phrase": "WIPE", "password": "pw-Secret-123"})
    assert not Patient.objects.all_including_deleted().exists()
    assert AuditLog.objects.filter(entity_type="Patient").exists()
    assert AuditLog.objects.filter(entity_type="FactoryReset", actor=su).exists()


# ─── seed_data ───────────────────────────────────────────────────

@pytest.mark.django_db
def test_seed_data_is_insert_only_and_never_creates_stock():
    """Issue: every deploy reset prices/ranges and injected random stock."""
    call_command("seed_data", verbosity=0)
    assert not StockItem.objects.exists()
    hgb = LabTest.objects.get(code="HGB")
    assert hgb.critical_low == Decimal("7.0")
    hgb.price_tzs = Decimal("4500")
    hgb.is_active = False
    hgb.save()
    cons = ServiceItem.objects.get(code="CONS-NEW")
    cons.unit_price_tzs = Decimal("6000")
    cons.save()
    Drug.objects.filter(generic_name="Ibuprofen").delete()

    call_command("seed_data", verbosity=0)
    hgb.refresh_from_db()
    cons.refresh_from_db()
    assert hgb.price_tzs == Decimal("4500") and hgb.is_active is False
    assert cons.unit_price_tzs == Decimal("6000")
    assert not Drug.objects.filter(generic_name="Ibuprofen").exists()
    assert not StockItem.objects.exists()
    assert ReasonCode.objects.filter(category="WAIVER").exists()


# ─── Pharmacy safety ─────────────────────────────────────────────

def test_allergy_class_matching(db, amoxicillin, paracetamol):
    """Issue: a 'penicillin' allergy didn't flag amoxicillin."""
    p = Patient(full_name="x", sex="M", allergies="Penicillin")
    assert check_allergy(p, amoxicillin) is not None
    assert check_allergy(p, paracetamol) is None
    assert check_allergy(Patient(full_name="x", sex="M", allergies="No known allergies"), amoxicillin) is None
    nsaid = Drug(generic_name="Ibuprofen", strength="400mg", form="TABLET")
    assert check_allergy(Patient(full_name="x", sex="M", allergies="NSAIDs"), nsaid) is not None


@pytest.mark.django_db
def test_rx_edit_runs_allergy_check(client, clinician, encounter, paid_invoice, amoxicillin, paracetamol):
    """Issue: editing a prescription to a different drug skipped the allergy check."""
    encounter.patient.allergies = "penicillin"
    encounter.patient.save()
    rx = Prescription.objects.create(
        encounter=encounter, drug=paracetamol, dose="1 tab", frequency="TDS",
        duration_days=3, quantity=9, prescribed_by=clinician,
    )
    client.force_login(clinician)
    url = reverse("pharmacy:rx_edit", args=[rx.pk])
    data = {"drug": str(amoxicillin.pk), "dose": "1 cap", "frequency": "TDS", "duration_days": "5", "quantity": "15"}
    resp = client.post(url, data)
    assert resp.status_code == 200 and b"allergy-alert" in resp.content
    rx.refresh_from_db()
    assert rx.drug_id == paracetamol.pk

    resp = client.post(url, {**data, "allergy_override": "on", "override_reason": "Tolerated before"})
    assert resp.status_code == 302
    rx.refresh_from_db()
    assert rx.drug_id == amoxicillin.pk and "ALLERGY OVERRIDE" in rx.instructions
    assert AuditLog.objects.filter(entity_id=rx.pk, metadata__change="allergy_override").exists()


@pytest.mark.django_db
def test_expired_batch_cannot_be_dispensed(client, pharmacist, clinician, encounter, paid_invoice, paracetamol):
    """Issue: expired stock could be dispensed."""
    expired = _batch(paracetamol, days=-10, number="OLD")
    rx = Prescription.objects.create(
        encounter=encounter, drug=paracetamol, dose="1", frequency="TDS",
        duration_days=3, quantity=9, prescribed_by=clinician,
    )
    client.force_login(pharmacist)
    client.post(reverse("pharmacy:rx_dispense", args=[rx.pk]), {"stock_item": str(expired.pk), "quantity": "9"})
    expired.refresh_from_db()
    assert expired.quantity_on_hand == 100
    assert not Dispense.objects.exists()


@pytest.mark.django_db
def test_resaving_a_dispense_does_not_deduct_twice(clinician, pharmacist, encounter, paracetamol):
    batch = _batch(paracetamol)
    rx = Prescription.objects.create(
        encounter=encounter, drug=paracetamol, dose="1", frequency="TDS",
        duration_days=3, quantity=9, prescribed_by=clinician,
    )
    d = Dispense(prescription=rx, stock_item=batch, quantity_dispensed=9, dispensed_by=pharmacist)
    d.save()
    d.patient_counselled = True
    d.save()
    batch.refresh_from_db()
    assert batch.quantity_on_hand == 91


@pytest.mark.django_db
def test_cancel_and_void_remove_the_charge(client, clinician, pharmacist, encounter, paid_invoice, paracetamol):
    """Issue: cancelled/voided prescriptions stayed on the invoice."""
    _batch(paracetamol)
    client.force_login(clinician)
    client.post(reverse("pharmacy:rx_prescribe", args=[encounter.pk]), {
        "drug": str(paracetamol.pk), "dose": "1", "frequency": "TDS", "duration_days": "3", "quantity": "10",
    })
    rx = Prescription.objects.get(encounter=encounter)
    paid_invoice.refresh_from_db()
    assert paid_invoice.total_tzs == Decimal("6000")
    assert paid_invoice.lines.filter(prescription=rx).exists()

    client.post(reverse("pharmacy:rx_cancel", args=[rx.pk]), {"reason": "Wrong drug"})
    paid_invoice.refresh_from_db()
    assert paid_invoice.total_tzs == Decimal("5000")
    assert paid_invoice.status == "PAID"


@pytest.mark.django_db
def test_stock_adjust_requires_quantity_and_records_reason(client, pharmacist, paracetamol):
    """Issue: a blank quantity zeroed stock and the reason was discarded."""
    batch = _batch(paracetamol)
    client.force_login(pharmacist)
    url = reverse("pharmacy:stock_adjust", args=[batch.pk])
    client.post(url, {"new_quantity": "", "reason": "count"})
    batch.refresh_from_db()
    assert batch.quantity_on_hand == 100

    client.post(url, {"new_quantity": "95", "reason": "5 broken bottles"})
    batch.refresh_from_db()
    assert batch.quantity_on_hand == 95
    movement = StockMovement.objects.get(stock_item=batch)
    assert movement.quantity == -5 and movement.notes == "5 broken bottles"


# ─── Lab ─────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_explicit_critical_limit_flags_result(client, lab_tech, clinician, encounter):
    """Issue: K+ 7.0 (range 3.5–5.1) was only HIGH under the generic rule."""
    potassium = LabTest.objects.create(
        code="K", name="Potassium", specimen_type="BLOOD",
        reference_range_min=Decimal("3.5"), reference_range_max=Decimal("5.1"),
        critical_low=Decimal("2.5"), critical_high=Decimal("6.0"),
    )
    order = LabOrder.objects.create(encounter=encounter, test=potassium, ordered_by=clinician, status="COLLECTED")
    client.force_login(lab_tech)
    client.post(reverse("lab:result_enter", args=[order.pk]), {"value_numeric": "7.0"})
    assert LabResult.objects.get(lab_order=order).flag == "CRITICAL"


@pytest.mark.django_db
def test_text_result_can_be_flagged_critical(client, lab_tech, clinician, encounter):
    """Issue: qualitative results (e.g. positive mRDT) could never be flagged."""
    mrdt = LabTest.objects.create(code="mRDT", name="Malaria RDT", specimen_type="BLOOD")
    order = LabOrder.objects.create(encounter=encounter, test=mrdt, ordered_by=clinician, status="COLLECTED")
    client.force_login(lab_tech)
    client.post(reverse("lab:result_enter", args=[order.pk]), {"value_text": "Positive (Pf)", "flag": "CRITICAL"})
    assert LabResult.objects.get(lab_order=order).flag == "CRITICAL"
    assert clinician.notifications.filter(kind="LAB_CRITICAL").exists()


@pytest.mark.django_db
def test_lab_tech_cannot_downgrade_numeric_flag(client, lab_tech, clinician, encounter):
    test = LabTest.objects.create(code="HGB", name="Hb", specimen_type="BLOOD",
                                  reference_range_min=Decimal("12"), reference_range_max=Decimal("16"),
                                  critical_low=Decimal("7"))
    order = LabOrder.objects.create(encounter=encounter, test=test, ordered_by=clinician, status="COLLECTED")
    client.force_login(lab_tech)
    client.post(reverse("lab:result_enter", args=[order.pk]), {"value_numeric": "5.0", "flag": "NORMAL"})
    assert LabResult.objects.get(lab_order=order).flag == "CRITICAL"


@pytest.mark.django_db
def test_lab_cancel_removes_charge(client, clinician, encounter, paid_invoice):
    test = LabTest.objects.create(code="FBP", name="Full blood picture", specimen_type="BLOOD", price_tzs=Decimal("10000"))
    client.force_login(clinician)
    client.post(reverse("lab:order_new", args=[encounter.pk]), {"tests": [str(test.pk)]})
    order = LabOrder.objects.get(encounter=encounter)
    paid_invoice.refresh_from_db()
    assert paid_invoice.total_tzs == Decimal("15000") and paid_invoice.status == "PARTIALLY_PAID"
    client.post(reverse("lab:cancel", args=[order.pk]), {"reason": "Ordered in error"})
    paid_invoice.refresh_from_db()
    assert paid_invoice.total_tzs == Decimal("5000") and paid_invoice.status == "PAID"


# ─── Billing ─────────────────────────────────────────────────────

@pytest.mark.django_db
def test_payment_cannot_be_voided_twice(client, receptionist, paid_invoice):
    """Issue: re-submitting the void form reversed the payment again."""
    pay = paid_invoice.payments.get()
    client.force_login(receptionist)
    url = reverse("billing:payment_void", args=[paid_invoice.pk, pay.pk])
    client.post(url, {"reason": "Wrong patient"})
    client.post(url, {"reason": "Wrong patient"})
    paid_invoice.refresh_from_db()
    assert paid_invoice.payments.filter(amount_tzs__lt=0).count() == 1
    assert paid_invoice.amount_paid_tzs == 0
    assert paid_invoice.status == "ISSUED"


@pytest.mark.django_db
def test_waiver_requires_reason_and_is_not_revenue(client, receptionist, encounter):
    inv = Invoice.objects.create(patient=encounter.patient, encounter=encounter, issued_by=receptionist, status="ISSUED")
    InvoiceLine.objects.create(invoice=inv, description="Consultation", quantity=Decimal("1"), unit_price_tzs=Decimal("5000"))
    inv.recalculate()
    reason = ReasonCode.objects.create(code="WAIVER-HARDSHIP", display_name="Hardship", category="WAIVER")
    client.force_login(receptionist)
    url = reverse("billing:payment_record", args=[inv.pk])

    client.post(url, {"method": "WAIVER", "amount_tzs": "5000"})
    assert not inv.payments.exists()
    client.post(url, {"method": "BITCOIN", "amount_tzs": "5000"})
    assert not inv.payments.exists()

    client.post(url, {"method": "WAIVER", "amount_tzs": "5000", "waiver_reason": str(reason.pk), "reference": "Approved by Dr X"})
    inv.refresh_from_db()
    assert inv.status == "PAID"
    assert AuditLog.objects.filter(entity_id=inv.pk, metadata__change="waiver").exists()


@pytest.mark.django_db
def test_auto_charge_ignores_non_consultation_invoices(receptionist, clinician, patient):
    """Issue: any encounter-less invoice (e.g. a pharmacy sale) was taken as
    the consultation prepay, so the consultation was never charged."""
    sale = Invoice.objects.create(patient=patient, issued_by=receptionist, status="ISSUED")
    InvoiceLine.objects.create(invoice=sale, description="ORS sachets", quantity=Decimal("2"), unit_price_tzs=Decimal("500"))
    sale.recalculate()

    enc = Encounter.objects.create(patient=patient, clinician=clinician, encounter_type="GENERAL")
    inv = auto_charge(enc, clinician)
    sale.refresh_from_db()
    assert inv.pk != sale.pk and sale.encounter_id is None
    assert inv.total_tzs == Decimal("5000")


@pytest.mark.django_db
def test_mh_referral_creates_its_own_invoice(receptionist, patient):
    """Issue: an open general prepay was returned for a mental-health referral."""
    general = prepay_consultation(patient, receptionist, encounter_type="GENERAL")
    mh = prepay_consultation(patient, receptionist, encounter_type="MENTAL_HEALTH")
    assert mh.pk != general.pk
    assert (general.prepay_type, mh.prepay_type) == ("GENERAL", "MENTAL_HEALTH")
    assert prepay_consultation(patient, receptionist, encounter_type="MENTAL_HEALTH").pk == mh.pk


@pytest.mark.django_db
def test_new_invoice_works_for_any_patient(client, receptionist):
    """Issue: the patient dropdown only listed the first 200 patients."""
    for i in range(205):
        Patient.objects.create(full_name=f"A{i:03d} Patient", sex="M", estimated_age=30)
    last = Patient.objects.create(full_name="Zuberi Zawadi", sex="M", estimated_age=30)
    client.force_login(receptionist)
    resp = client.get(reverse("billing:invoice_new") + f"?patient={last.pk}")
    assert b"Zuberi Zawadi" in resp.content
    resp = client.post(reverse("billing:invoice_new"), {"patient_id": str(last.pk)})
    assert resp.status_code == 302
    assert Invoice.objects.filter(patient=last).exists()


@pytest.mark.django_db
def test_bad_ids_return_404_not_500(client, receptionist):
    client.force_login(receptionist)
    assert client.get(reverse("billing:invoice_new") + "?patient=not-a-uuid").status_code == 404
    assert client.get(reverse("billing:invoice_list") + "?patient=123").status_code == 404


# ─── Encounters ──────────────────────────────────────────────────

@pytest.mark.django_db
def test_general_flow_cannot_create_mental_health_encounter(client, clinician, patient):
    """Issue: encounter_type came straight from POST."""
    client.force_login(clinician)
    client.post(reverse("encounters:new") + f"?patient={patient.pk}", {"encounter_type": "MENTAL_HEALTH"})
    client.post(reverse("encounters:new") + f"?patient={patient.pk}", {"encounter_type": "NONSENSE"})
    assert not Encounter.objects.exists()
    client.post(reverse("encounters:new") + f"?patient={patient.pk}", {"encounter_type": "ANC"})
    assert Encounter.objects.get().encounter_type == "ANC"


@pytest.mark.django_db
def test_invalid_vitals_are_rejected_not_500(client, clinician, encounter, paid_invoice):
    """Issue: raw POST strings went into numeric fields (typo → 500)."""
    client.force_login(clinician)
    url = reverse("encounters:add_vitals", args=[encounter.pk])
    resp = client.post(url, {"temperature": "37..5", "pulse": "-4"}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200 and b"vitals-error" in resp.content
    assert not Vitals.objects.exists()
    client.post(url, {"temperature": "37,5", "pulse": "88", "bp_systolic": "120", "bp_diastolic": "80"})
    v = Vitals.objects.get()
    assert v.temperature == Decimal("37.5") and v.pulse == 88


@pytest.mark.django_db
def test_encounter_notes_need_clinical_role(client, encounter, lab_tech, pharmacist, receptionist):
    """Issue: any logged-in user could read SOAP notes."""
    for user in (lab_tech, pharmacist, receptionist):
        client.force_login(user)
        assert client.get(reverse("encounters:detail", args=[encounter.pk])).status_code == 403


@pytest.mark.django_db
def test_viewing_does_not_reassign_nurse_encounter(client, nurse, clinician, patient):
    """Issue: a GET reassigned ownership of nurse-started encounters."""
    enc = Encounter.objects.create(patient=patient, clinician=nurse, encounter_type="GENERAL")
    client.force_login(clinician)
    client.get(reverse("encounters:detail", args=[enc.pk]))
    enc.refresh_from_db()
    assert enc.clinician_id == nurse.pk
    client.post(reverse("encounters:save_draft", args=[enc.pk]), {"subjective": "Headache x2d"})
    enc.refresh_from_db()
    assert enc.clinician_id == clinician.pk


@pytest.mark.django_db
def test_attachment_upload_validates_type(client, clinician, encounter, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    client.force_login(clinician)
    url = reverse("encounters:attachment_upload", args=[encounter.pk])
    client.post(url, {"label": "Bad", "file": SimpleUploadedFile("x.exe", b"MZ...")})
    client.post(url, {"label": "Fake", "file": SimpleUploadedFile("x.pdf", b"not a pdf")})
    assert not EncounterAttachment.objects.exists()
    client.post(url, {"label": "Referral", "file": SimpleUploadedFile("r.pdf", b"%PDF-1.4 ok")})
    assert EncounterAttachment.objects.get().label == "Referral"


# ─── Patients ────────────────────────────────────────────────────

@pytest.mark.django_db
def test_patient_list_shows_every_patient(client, clinician):
    """Issue: only the 50 most recent patients were listed."""
    for i in range(60):
        Patient.objects.create(full_name=f"Patient {i:02d}", sex="F", estimated_age=20)
    client.force_login(clinician)
    resp = client.get(reverse("patients:list"))
    assert resp.context["total_count"] == 60
    assert resp.context["page_obj"].paginator.num_pages == 3
    resp = client.get(reverse("patients:list") + "?page=3&sort=name")
    names = [p.full_name for p in resp.context["patients"]]
    assert names == [f"Patient {i:02d}" for i in range(50, 60)]


@pytest.mark.django_db
def test_patient_list_filters(client, clinician, patient):
    Patient.objects.create(full_name="Child", sex="M", estimated_age=4)
    Encounter.objects.create(patient=patient, clinician=clinician)
    client.force_login(clinician)
    resp = client.get(reverse("patients:list") + "?flag=minor")
    assert [p.full_name for p in resp.context["patients"]] == ["Child"]
    resp = client.get(reverse("patients:list") + "?visited=never")
    assert [p.full_name for p in resp.context["patients"]] == ["Child"]
    resp = client.get(reverse("patients:list") + "?visited=30d")
    assert [p.full_name for p in resp.context["patients"]] == ["Asha Mwangi"]
    assert resp.context["patients"][0].visit_count == 1


@pytest.mark.django_db
def test_chart_hides_mh_prescriptions_from_other_roles(client, nurse, counsellor, patient, paracetamol):
    """Issue: chart listed prescriptions/labs from mental-health encounters."""
    mh = Encounter.objects.create(patient=patient, clinician=counsellor, encounter_type="MENTAL_HEALTH")
    Prescription.objects.create(encounter=mh, drug=paracetamol, dose="1", frequency="OD",
                                duration_days=1, quantity=1, prescribed_by=counsellor)
    client.force_login(nurse)
    resp = client.get(reverse("patients:chart", args=[patient.pk]) + "?tab=prescriptions")
    assert list(resp.context["prescriptions"]) == []
    client.force_login(counsellor)
    resp = client.get(reverse("patients:chart", args=[patient.pk]) + "?tab=prescriptions")
    assert len(resp.context["prescriptions"]) == 1


@pytest.mark.django_db
def test_chart_tabs_follow_role(client, pharmacist, receptionist, patient):
    client.force_login(receptionist)
    assert client.get(reverse("patients:chart", args=[patient.pk])).status_code == 403
    client.force_login(pharmacist)
    resp = client.get(reverse("patients:chart", args=[patient.pk]) + "?tab=encounters")
    assert [t[0] for t in resp.context["tabs"]] == ["prescriptions"]
    assert resp.context["active_tab"] == "prescriptions"
    assert list(resp.context["encounters"]) == []


@pytest.mark.django_db
def test_patient_number_ignores_hand_entered_numbers():
    Patient.objects.create(full_name="a", sex="M", estimated_age=1, patient_number=f"PT-{timezone.localdate().year}-ABC")
    p = Patient.objects.create(full_name="b", sex="M", estimated_age=1)
    q = Patient.objects.create(full_name="c", sex="M", estimated_age=1)
    year = timezone.localdate().year
    assert (p.patient_number, q.patient_number) == (f"PT-{year}-000001", f"PT-{year}-000002")


# ─── Smoke: every page touched by the fixes renders ──────────────

@pytest.mark.django_db
def test_changed_pages_render(client, admin_user, clinician, encounter, paid_invoice, paracetamol):
    batch = _batch(paracetamol)
    _batch(paracetamol, days=-5, number="EXPIRED-1")
    StockMovement.objects.create(stock_item=batch, movement_type="RECEIVE", quantity=100, performed_by=admin_user)
    rx = Prescription.objects.create(
        encounter=encounter, drug=paracetamol, dose="1", frequency="TDS",
        duration_days=3, quantity=9, prescribed_by=clinician,
    )
    test = LabTest.objects.create(code="HGB", name="Hb", specimen_type="BLOOD", critical_low=Decimal("7"))
    order = LabOrder.objects.create(encounter=encounter, test=test, ordered_by=clinician, status="COLLECTED")
    client.force_login(admin_user)
    pages = [
        reverse("pharmacy:drug_detail", args=[paracetamol.pk]),
        reverse("pharmacy:rx_dispense", args=[rx.pk]),
        reverse("pharmacy:rx_edit", args=[rx.pk]),
        reverse("lab:test_edit", args=[test.pk]),
        reverse("lab:order_detail", args=[order.pk]),
        reverse("billing:invoice_detail", args=[paid_invoice.pk]),
        reverse("billing:invoice_list"),
        reverse("billing:invoice_new"),
        reverse("encounters:detail", args=[encounter.pk]),
        reverse("patients:chart", args=[encounter.patient.pk]) + "?tab=documents",
        reverse("patients:list") + "?q=asha&sort=last_visit",
        reverse("dashboard"),
    ]
    for url in pages:
        resp = client.get(url)
        assert resp.status_code == 200, url
    detail = client.get(reverse("pharmacy:rx_dispense", args=[rx.pk])).content
    assert b"EXPIRED-1" in detail  # warned about, but not offered as a batch
