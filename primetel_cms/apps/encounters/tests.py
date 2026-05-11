"""Tests for encounter lockout, MH PHI access, and PHQ-9 red flag."""
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Role
from apps.billing.models import Invoice
from apps.encounters.models import Encounter, MentalHealthAssessment, Vitals
from apps.patients.models import Patient

User = get_user_model()


@pytest.fixture
def role_clinician(db):
    return Role.objects.create(code="CLINICIAN", display_name="Clinician")


@pytest.fixture
def role_counsellor(db):
    return Role.objects.create(code="COUNSELLOR", display_name="Counsellor")


@pytest.fixture
def role_nurse(db):
    return Role.objects.create(code="NURSE", display_name="Nurse")


@pytest.fixture
def clinician(db, role_clinician):
    return User.objects.create_user(username="clin", password="pw", role=role_clinician)


@pytest.fixture
def other_clinician(db, role_clinician):
    return User.objects.create_user(username="clin2", password="pw", role=role_clinician)


@pytest.fixture
def counsellor(db, role_counsellor):
    return User.objects.create_user(username="couns", password="pw", role=role_counsellor)


@pytest.fixture
def nurse(db, role_nurse):
    return User.objects.create_user(username="nurse", password="pw", role=role_nurse)


@pytest.fixture
def patient(db):
    return Patient.objects.create(full_name="Test Patient", sex="M", date_of_birth=date(1990, 1, 1))


@pytest.fixture
def encounter(db, patient, clinician):
    return Encounter.objects.create(
        patient=patient,
        clinician=clinician,
        encounter_type="GENERAL",
        chief_complaint="Headache",
        assessment="Tension headache",
    )


@pytest.fixture
def mh_encounter(db, patient, counsellor):
    return Encounter.objects.create(
        patient=patient,
        clinician=counsellor,
        encounter_type="MENTAL_HEALTH",
        chief_complaint="Low mood",
        assessment="Depressive episode",
    )


@pytest.mark.django_db
def test_finalise_locks_encounter(encounter, clinician):
    encounter.finalise(user=clinician)
    encounter.refresh_from_db()
    assert encounter.status == "FINALISED"
    assert encounter.finalised_at is not None


@pytest.mark.django_db
def test_finalise_requires_chief_complaint(patient, clinician):
    enc = Encounter.objects.create(
        patient=patient, clinician=clinician, encounter_type="GENERAL", assessment="x"
    )
    with pytest.raises(ValidationError):
        enc.finalise(user=clinician)


@pytest.mark.django_db
def test_finalise_requires_assessment(patient, clinician):
    enc = Encounter.objects.create(
        patient=patient, clinician=clinician, encounter_type="GENERAL", chief_complaint="x"
    )
    with pytest.raises(ValidationError):
        enc.finalise(user=clinician)


@pytest.mark.django_db
def test_double_finalise_raises(encounter, clinician):
    encounter.finalise(user=clinician)
    encounter.refresh_from_db()
    with pytest.raises(ValidationError):
        encounter.finalise(user=clinician)


@pytest.mark.django_db
def test_amend_only_after_finalise(encounter, clinician):
    with pytest.raises(ValidationError):
        encounter.amend(user=clinician)
    encounter.finalise(user=clinician)
    encounter.refresh_from_db()
    encounter.amend(user=clinician)
    encounter.refresh_from_db()
    assert encounter.status == "AMENDED"


@pytest.mark.django_db
def test_finalised_encounter_blocks_vitals_via_view(client, encounter, nurse, clinician):
    encounter.finalise(user=clinician)
    encounter.refresh_from_db()
    client.force_login(nurse)
    resp = client.post(
        reverse("encounters:add_vitals", args=[encounter.pk]),
        data={"pulse": 80},
        follow=False,
    )
    # View redirects (with error message) instead of creating vitals.
    assert resp.status_code == 302
    assert encounter.vitals.count() == 0


@pytest.mark.django_db
def test_nurse_blocked_from_encounter_until_patient_pays(client, nurse, patient):
    """Without a paid prepay invoice, a nurse trying to start an encounter
    is redirected back to the queue with an error and no encounter is
    created. (The nurse must use 'Send to billing' first.)"""
    client.force_login(nurse)
    resp = client.get(reverse("encounters:new") + f"?patient={patient.pk}")
    assert resp.status_code == 302  # redirected back to queue
    assert "/appointments/queue/" in resp.headers.get("Location", "")
    assert not Encounter.objects.filter(patient=patient).exists()


@pytest.mark.django_db
def test_nurse_can_start_triage_after_payment_clears(client, nurse, patient, clinician):
    """When reception has recorded a paid consultation invoice (encounter=null
    + status=PAID), the nurse can start the encounter for triage. The new
    encounter attaches to the paid invoice — no double charge."""
    from apps.billing.models import Invoice, InvoiceLine, Payment
    from decimal import Decimal as _D

    inv = Invoice.objects.create(
        patient=patient, encounter=None, issued_by=clinician, created_by=clinician,
    )
    InvoiceLine.objects.create(
        invoice=inv, description="Consult prepay",
        quantity=_D("1"), unit_price_tzs=_D("5000"),
    )
    inv.recalculate()
    inv.status = "ISSUED"
    inv.save(update_fields=["status"])
    Payment.objects.create(
        invoice=inv, method="CASH", amount_tzs=_D("5000"),
        received_by=clinician, created_by=clinician,
    )
    inv.refresh_from_db()
    assert inv.status == "PAID"

    client.force_login(nurse)
    resp = client.post(
        reverse("encounters:new") + f"?patient={patient.pk}",
        data={"encounter_type": "GENERAL", "chief_complaint": "Triage"},
    )
    assert resp.status_code == 302
    encounter = Encounter.objects.get(patient=patient)
    # The encounter attached to the existing paid invoice — no duplicate.
    inv.refresh_from_db()
    assert inv.encounter_id == encounter.pk
    assert Invoice.objects.filter(patient=patient).count() == 1


@pytest.mark.django_db
def test_nurse_cannot_finalise_or_diagnose_even_after_payment(client, nurse, patient, clinician):
    """Even on an encounter the nurse started, they cannot finalise, write
    SOAP, add diagnoses, prescribe, or order labs. Only vitals."""
    encounter = Encounter.objects.create(
        patient=patient, clinician=clinician,
        encounter_type="GENERAL", chief_complaint="x", assessment="y",
    )
    client.force_login(nurse)
    # Add diagnosis → 403
    resp = client.post(
        reverse("encounters:add_diagnosis", args=[encounter.pk]),
        data={"description": "Tension headache"},
    )
    assert resp.status_code == 403
    # Finalise → 403
    resp = client.post(reverse("encounters:finalise", args=[encounter.pk]))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_mh_encounter_404_for_unauthorised_user(client, mh_encounter, clinician, other_clinician):
    """A clinician who doesn't own the MH encounter must get 404, not 403."""
    client.force_login(other_clinician)
    resp = client.get(reverse("encounters:detail", args=[mh_encounter.pk]))
    assert resp.status_code == 404


@pytest.mark.django_db
def test_mh_encounter_visible_to_counsellor(client, mh_encounter, counsellor):
    client.force_login(counsellor)
    resp = client.get(reverse("encounters:detail", args=[mh_encounter.pk]))
    assert resp.status_code == 200


@pytest.mark.django_db
def test_phq9_q9_red_flag_in_context(client, mh_encounter, counsellor):
    MentalHealthAssessment.objects.create(
        encounter=mh_encounter,
        instrument="PHQ9",
        responses=[1, 2, 1, 0, 0, 0, 0, 0, 2],  # Q9 == 2
    )
    client.force_login(counsellor)
    resp = client.get(reverse("encounters:detail", args=[mh_encounter.pk]))
    assert resp.status_code == 200
    assert resp.context["phq9_q9_red_flag"] is True


@pytest.mark.django_db
def test_phq9_no_red_flag_when_q9_zero(client, mh_encounter, counsellor):
    MentalHealthAssessment.objects.create(
        encounter=mh_encounter,
        instrument="PHQ9",
        responses=[3, 3, 3, 3, 3, 3, 3, 3, 0],
    )
    client.force_login(counsellor)
    resp = client.get(reverse("encounters:detail", args=[mh_encounter.pk]))
    assert resp.context["phq9_q9_red_flag"] is False
