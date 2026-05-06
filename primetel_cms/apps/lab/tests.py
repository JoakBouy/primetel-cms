"""Tests for lab status transitions and result auto-flagging."""
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from apps.accounts.models import Role
from apps.encounters.models import Encounter
from apps.lab.models import LabOrder, LabResult, LabTest
from apps.lab.views import _compute_flag
from apps.patients.models import Patient

User = get_user_model()


@pytest.fixture
def role_lab(db):
    return Role.objects.create(code="LAB", display_name="Lab")


@pytest.fixture
def lab_user(db, role_lab):
    return User.objects.create_user(username="lab", password="pw", role=role_lab)


@pytest.fixture
def patient(db):
    return Patient.objects.create(full_name="Lab Patient", sex="M", date_of_birth=date(1990, 1, 1))


@pytest.fixture
def clinician(db):
    return User.objects.create_user(username="clinx", password="pw")


@pytest.fixture
def encounter(db, patient, clinician):
    return Encounter.objects.create(patient=patient, clinician=clinician)


@pytest.fixture
def hgb_test(db):
    return LabTest.objects.create(
        code="HGB", name="Haemoglobin", specimen_type="BLOOD",
        reference_range_min=Decimal("12"), reference_range_max=Decimal("16"),
        reference_unit="g/dL",
    )


@pytest.fixture
def order(db, encounter, hgb_test, clinician):
    return LabOrder.objects.create(encounter=encounter, test=hgb_test, ordered_by=clinician)


@pytest.mark.django_db
def test_compute_flag_normal(order):
    assert _compute_flag(order, Decimal("14")) == "NORMAL"


@pytest.mark.django_db
def test_compute_flag_low(order):
    assert _compute_flag(order, Decimal("10")) == "LOW"


@pytest.mark.django_db
def test_compute_flag_high(order):
    assert _compute_flag(order, Decimal("17")) == "HIGH"


@pytest.mark.django_db
def test_compute_flag_critical_low(order):
    # 25% below the lower bound (12 * 0.75 = 9) — anything below is critical.
    assert _compute_flag(order, Decimal("8")) == "CRITICAL"


@pytest.mark.django_db
def test_compute_flag_critical_high(order):
    # 1.5 * upper bound = 24 — anything above is critical.
    assert _compute_flag(order, Decimal("30")) == "CRITICAL"


@pytest.mark.django_db
def test_lab_collect_transitions_status(client, order, lab_user):
    client.force_login(lab_user)
    resp = client.post(reverse("lab:collect", args=[order.pk]))
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.status == "COLLECTED"
    assert order.collected_at is not None


@pytest.mark.django_db
def test_lab_result_entry_sets_flag_and_status(client, order, lab_user):
    client.force_login(lab_user)
    resp = client.post(
        reverse("lab:result_enter", args=[order.pk]),
        data={"value_numeric": "9.0"},
    )
    assert resp.status_code == 302
    order.refresh_from_db()
    assert order.status == "RESULTED"
    assert order.result.flag == "LOW"
    assert order.result.value_numeric == Decimal("9.0")


@pytest.mark.django_db
def test_lab_result_requires_value(client, order, lab_user):
    client.force_login(lab_user)
    resp = client.post(reverse("lab:result_enter", args=[order.pk]), data={})
    assert resp.status_code == 302
    assert not LabResult.objects.filter(lab_order=order).exists()


@pytest.mark.django_db
def test_lab_detail_shows_collected_state(client, order, lab_user):
    client.force_login(lab_user)
    order.status = "COLLECTED"
    order.save(update_fields=["status"])

    resp = client.get(reverse("lab:order_detail", args=[order.pk]))

    assert resp.status_code == 200
    assert b"Sample collected" in resp.content
    assert b"lab-collected-state" in resp.content


@pytest.mark.django_db
def test_lab_print_uses_patient_number_label(order):
    html = render_to_string("lab/order_print.html", {"order": order, "result": None})

    assert "Patient No." in html
    assert order.encounter.patient.patient_number in html


@pytest.mark.django_db
def test_encounter_dashboard_shows_lab_result(client, order, clinician, lab_user):
    LabResult.objects.create(
        lab_order=order,
        value_numeric=Decimal("13.5"),
        flag="NORMAL",
        performed_by=lab_user,
    )
    order.status = "RESULTED"
    order.save(update_fields=["status"])

    client.force_login(clinician)
    resp = client.get(reverse("encounters:detail", args=[order.encounter.pk]))

    assert resp.status_code == 200
    assert b"13.5" in resp.content
    assert b"Normal" in resp.content
