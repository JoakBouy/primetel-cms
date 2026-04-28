"""Tests for billing math and payment lifecycle."""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from apps.billing.models import Invoice, InvoiceLine, Payment
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
