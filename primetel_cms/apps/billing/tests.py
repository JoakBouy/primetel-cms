"""Tests for billing math, payment lifecycle, and consultation auto-charge."""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.billing.consultation import auto_charge, consultation_code
from apps.billing.models import Invoice, InvoiceLine, Payment, ServiceItem
from apps.encounters.models import Encounter
from apps.patients.models import Patient

User = get_user_model()


@pytest.fixture
def cashier(db):
    return User.objects.create_user(username="cashier", password="pw")


@pytest.fixture
def patient(db):
    return Patient.objects.create(full_name="Pay Patient", sex="F", date_of_birth=date(1985, 5, 5))


@pytest.fixture
def invoice(db, patient, cashier):
    inv = Invoice.objects.create(patient=patient, issued_by=cashier)
    InvoiceLine.objects.create(invoice=inv, description="Consultation", quantity=Decimal("1"), unit_price_tzs=Decimal("5000"))
    InvoiceLine.objects.create(invoice=inv, description="Lab fee", quantity=Decimal("1"), unit_price_tzs=Decimal("3000"))
    inv.recalculate()
    inv.status = "ISSUED"
    inv.save()
    return inv


@pytest.mark.django_db
def test_recalculate_sums_lines(invoice):
    assert invoice.subtotal_tzs == Decimal("8000")
    assert invoice.total_tzs == Decimal("8000")


@pytest.mark.django_db
def test_partial_payment_marks_partially_paid(invoice, cashier):
    Payment.objects.create(invoice=invoice, method="CASH", amount_tzs=Decimal("3000"), received_by=cashier)
    invoice.refresh_from_db()
    assert invoice.amount_paid_tzs == Decimal("3000")
    assert invoice.status == "PARTIALLY_PAID"
    assert invoice.balance_tzs == Decimal("5000")


@pytest.mark.django_db
def test_full_payment_marks_paid(invoice, cashier):
    Payment.objects.create(invoice=invoice, method="CASH", amount_tzs=Decimal("8000"), received_by=cashier)
    invoice.refresh_from_db()
    assert invoice.status == "PAID"
    assert invoice.balance_tzs == Decimal("0")


@pytest.mark.django_db
def test_overpayment_still_marks_paid(invoice, cashier):
    Payment.objects.create(invoice=invoice, method="CASH", amount_tzs=Decimal("9000"), received_by=cashier)
    invoice.refresh_from_db()
    assert invoice.status == "PAID"
    assert invoice.balance_tzs == Decimal("-1000")


@pytest.mark.django_db
def test_split_payments_sum_correctly(invoice, cashier):
    Payment.objects.create(invoice=invoice, method="CASH", amount_tzs=Decimal("3000"), received_by=cashier)
    Payment.objects.create(invoice=invoice, method="MPESA", amount_tzs=Decimal("5000"), reference="QWE123", received_by=cashier)
    invoice.refresh_from_db()
    assert invoice.amount_paid_tzs == Decimal("8000")
    assert invoice.status == "PAID"


@pytest.mark.django_db
def test_payment_blocked_on_cancelled_invoice(invoice, cashier):
    invoice.status = "CANCELLED"
    invoice.save()
    with pytest.raises(ValidationError):
        Payment.objects.create(invoice=invoice, method="CASH", amount_tzs=Decimal("1000"), received_by=cashier)


# ─── Consultation auto-charge ────────────────────────────────────


@pytest.fixture
def patient_two(db):
    return Patient.objects.create(full_name="Returning Patient", sex="M", date_of_birth=date(1980, 1, 1))


@pytest.fixture
def cons_new_service(db):
    return ServiceItem.objects.create(
        code="CONS-NEW", name="New patient consultation",
        category="CONSULT", unit_price_tzs=Decimal("5000"), is_active=True,
    )


@pytest.fixture
def cons_fu_service(db):
    return ServiceItem.objects.create(
        code="CONS-FU", name="Follow-up consultation",
        category="CONSULT", unit_price_tzs=Decimal("3000"), is_active=True,
    )


@pytest.mark.django_db
def test_first_encounter_charged_as_new(patient_two, cashier, cons_new_service, cons_fu_service):
    enc = Encounter.objects.create(patient=patient_two, clinician=cashier, encounter_type="GENERAL")
    assert consultation_code(enc) == "CONS-NEW"
    inv = auto_charge(enc, cashier)
    assert inv is not None
    assert inv.total_tzs == Decimal("5000")
    line = inv.lines.get()
    assert line.unit_price_tzs == Decimal("5000")
    assert "New patient" in line.description


@pytest.mark.django_db
def test_followup_after_finalised_encounter(patient_two, cashier, cons_new_service, cons_fu_service):
    # Pre-existing finalised encounter makes this patient a follow-up.
    Encounter.objects.create(
        patient=patient_two, clinician=cashier, encounter_type="GENERAL",
        chief_complaint="x", assessment="y", status="FINALISED",
    )
    enc = Encounter.objects.create(patient=patient_two, clinician=cashier, encounter_type="GENERAL")
    assert consultation_code(enc) == "CONS-FU"
    inv = auto_charge(enc, cashier)
    assert inv.total_tzs == Decimal("3000")


@pytest.mark.django_db
def test_first_antenatal_consultation_uses_new_patient_price(patient_two, cashier, cons_fu_service):
    Encounter.objects.create(
        patient=patient_two, clinician=cashier, encounter_type="GENERAL",
        chief_complaint="x", assessment="y", status="FINALISED",
    )
    ServiceItem.objects.create(
        code="CONS-ANC", name="First antenatal consultation",
        category="CONSULT", unit_price_tzs=Decimal("5000"), is_active=True,
    )

    enc = Encounter.objects.create(patient=patient_two, clinician=cashier, encounter_type="ANC")

    assert consultation_code(enc) == "CONS-ANC"
    inv = auto_charge(enc, cashier)
    assert inv.total_tzs == Decimal("5000")


@pytest.mark.django_db
def test_mh_consultation_uses_mh_price(patient_two, cashier):
    ServiceItem.objects.create(
        code="CONS-MH", name="MH consult", category="CONSULT",
        unit_price_tzs=Decimal("10000"), is_active=True,
    )
    enc = Encounter.objects.create(patient=patient_two, clinician=cashier, encounter_type="MENTAL_HEALTH")
    assert consultation_code(enc) == "CONS-MH"
    inv = auto_charge(enc, cashier)
    assert inv.total_tzs == Decimal("10000")


@pytest.mark.django_db
def test_auto_charge_is_idempotent(patient_two, cashier, cons_new_service, cons_fu_service):
    enc = Encounter.objects.create(patient=patient_two, clinician=cashier, encounter_type="GENERAL")
    inv1 = auto_charge(enc, cashier)
    inv2 = auto_charge(enc, cashier)
    assert inv1.pk == inv2.pk
    assert Invoice.objects.filter(encounter=enc).count() == 1


@pytest.mark.django_db
def test_auto_charge_falls_back_when_service_missing(patient_two, cashier):
    # No ServiceItem exists — should still create an invoice using DEFAULTS.
    enc = Encounter.objects.create(patient=patient_two, clinician=cashier, encounter_type="GENERAL")
    inv = auto_charge(enc, cashier)
    assert inv is not None
    assert inv.total_tzs == Decimal("5000")
