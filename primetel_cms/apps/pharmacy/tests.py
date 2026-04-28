import pytest
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from apps.pharmacy.models import Drug, StockItem, Prescription, Dispense
from apps.encounters.models import Encounter
from apps.patients.models import Patient

User = get_user_model()

@pytest.fixture
def user():
    return User.objects.create_user(username="testuser", password="password")

@pytest.fixture
def patient():
    return Patient.objects.create(
        full_name="Test Patient", sex="M", date_of_birth=timezone.now().date()
    )

@pytest.fixture
def encounter(patient, user):
    return Encounter.objects.create(
        patient=patient, clinician=user, encounter_type="GENERAL"
    )

@pytest.fixture
def drug():
    return Drug.objects.create(
        generic_name="Test Drug", strength="500mg", form="TABLET", unit_price_tzs=Decimal("100"), low_stock_threshold=10
    )

@pytest.fixture
def stock_item(drug):
    return StockItem.objects.create(
        drug=drug, batch_number="B1", quantity_on_hand=100, expiry_date=timezone.now().date() + timedelta(days=365)
    )

@pytest.fixture
def prescription(encounter, drug, user):
    return Prescription.objects.create(
        encounter=encounter, drug=drug, dose="1 tablet", frequency="BID", duration_days=5, quantity=10, prescribed_by=user
    )

@pytest.mark.django_db
def test_dispense_atomic_deduction(prescription, stock_item, user):
    """Test that dispensing deducts stock atomically and updates status."""
    dispense = Dispense(
        prescription=prescription,
        stock_item=stock_item,
        quantity_dispensed=10,
        dispensed_by=user
    )
    dispense.save()

    stock_item.refresh_from_db()
    prescription.refresh_from_db()

    assert stock_item.quantity_on_hand == 90
    assert prescription.status == "DISPENSED"
    assert dispense.pk is not None

@pytest.mark.django_db
def test_dispense_insufficient_stock(prescription, stock_item, user):
    """Test that dispensing fails if insufficient stock."""
    dispense = Dispense(
        prescription=prescription,
        stock_item=stock_item,
        quantity_dispensed=150,
        dispensed_by=user
    )
    
    with pytest.raises(ValidationError):
        dispense.save()

    stock_item.refresh_from_db()
    prescription.refresh_from_db()

    assert stock_item.quantity_on_hand == 100
    assert prescription.status == "PRESCRIBED"
