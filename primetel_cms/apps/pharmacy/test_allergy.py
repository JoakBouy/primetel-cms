"""Tests for the drug-allergy check."""
from datetime import date
from decimal import Decimal

import pytest

from apps.patients.models import Patient
from apps.pharmacy.allergy import check_allergy
from apps.pharmacy.models import Drug


@pytest.fixture
def amoxicillin(db):
    return Drug.objects.create(generic_name="Amoxicillin", strength="500mg", form="CAPSULE", unit_price_tzs=Decimal("200"))


@pytest.fixture
def paracetamol(db):
    return Drug.objects.create(generic_name="Paracetamol", strength="500mg", form="TABLET", unit_price_tzs=Decimal("100"))


@pytest.mark.django_db
def test_match_on_generic_name(amoxicillin):
    p = Patient.objects.create(full_name="x", sex="M", date_of_birth=date(1990, 1, 1), allergies="amoxicillin, peanuts")
    assert check_allergy(p, amoxicillin) is not None


@pytest.mark.django_db
def test_no_match_on_unrelated_drug(paracetamol):
    p = Patient.objects.create(full_name="x", sex="M", date_of_birth=date(1990, 1, 1), allergies="amoxicillin")
    assert check_allergy(p, paracetamol) is None


@pytest.mark.django_db
def test_blank_allergies_returns_none(amoxicillin):
    p = Patient.objects.create(full_name="x", sex="M", date_of_birth=date(1990, 1, 1), allergies="")
    assert check_allergy(p, amoxicillin) is None


@pytest.mark.django_db
def test_stopwords_dont_trigger(amoxicillin):
    p = Patient.objects.create(full_name="x", sex="M", date_of_birth=date(1990, 1, 1), allergies="No known allergies")
    # "no", "known", "allergies" are all stopwords/short — must not match.
    assert check_allergy(p, amoxicillin) is None


@pytest.mark.django_db
def test_brand_name_match():
    drug = Drug.objects.create(
        generic_name="Sulfamethoxazole/Trimethoprim",
        brand_name="Bactrim",
        strength="800/160mg",
        form="TABLET",
        unit_price_tzs=Decimal("150"),
    )
    p = Patient.objects.create(full_name="x", sex="M", date_of_birth=date(1990, 1, 1), allergies="bactrim")
    assert check_allergy(p, drug) is not None


@pytest.mark.django_db
def test_case_insensitive(amoxicillin):
    p = Patient.objects.create(full_name="x", sex="M", date_of_birth=date(1990, 1, 1), allergies="AMOXICILLIN")
    assert check_allergy(p, amoxicillin) is not None
