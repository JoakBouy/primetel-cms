"""Tests for the reports section: periods, role-scoped sections, CSV export,
and the visit register."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.core.models import AuditLog
from apps.encounters.models import Diagnosis, Encounter
from apps.patients.models import Patient
from apps.reports.periods import Period, period_for, period_from_request

User = get_user_model()


def _user(code, username):
    role, _ = Role.objects.get_or_create(code=code, defaults={"display_name": code.title()})
    return User.objects.create_user(username=username, password="pw", role=role, full_name=username.title())


def _at(day, hour=10):
    """Aware datetime on a local date (so tests don't depend on UTC offset)."""
    return timezone.make_aware(datetime.combine(day, time(hour)))


# ─── Periods ─────────────────────────────────────────────────────

def test_period_boundaries():
    anchor = date(2026, 5, 14)  # a Thursday
    assert period_for("week", anchor) == Period("week", date(2026, 5, 11), date(2026, 5, 17))
    assert period_for("month", anchor) == Period("month", date(2026, 5, 1), date(2026, 5, 31))
    assert period_for("quarter", anchor) == Period("quarter", date(2026, 4, 1), date(2026, 6, 30))
    assert period_for("year", anchor) == Period("year", date(2026, 1, 1), date(2026, 12, 31))
    assert period_for("quarter", date(2026, 2, 1)).label == "Q1 2026"


def test_period_navigation_crosses_years():
    q1 = period_for("quarter", date(2026, 1, 10))
    assert q1.previous == Period("quarter", date(2025, 10, 1), date(2025, 12, 31))
    assert q1.previous.next == q1
    jan = period_for("month", date(2026, 1, 31))
    assert jan.previous.start == date(2025, 12, 1)
    assert period_for("year", date(2026, 6, 1)).next.start == date(2027, 1, 1)
    custom = Period("custom", date(2026, 3, 1), date(2026, 3, 10))
    assert custom.previous == Period("custom", date(2026, 2, 19), date(2026, 2, 28))


def test_period_from_request_handles_bad_input():
    rf = RequestFactory()
    p = period_from_request(rf.get("/", {"period": "nonsense", "date": "garbage"}))
    assert p.kind == "month" and p.start.day == 1
    p = period_from_request(rf.get("/", {"period": "custom", "start": "2026-03-10", "end": "2026-03-01"}))
    assert (p.start, p.end) == (date(2026, 3, 1), date(2026, 3, 10))  # swapped into order
    p = period_from_request(rf.get("/", {"period": "custom", "start": "2026-03-10"}))
    assert p.kind == "month"  # incomplete custom falls back


def test_trend_buckets_cover_period():
    assert len(period_for("week", date(2026, 5, 14)).buckets()) == 7
    assert len(period_for("month", date(2026, 2, 3)).buckets()) == 28
    assert len(period_for("year", date(2026, 2, 3)).buckets()) == 12
    assert period_for("quarter", date(2026, 2, 3)).bucket == "week"


# ─── Reports page ────────────────────────────────────────────────

@pytest.fixture
def setup_data(db):
    clinician = _user("CLINICIAN", "clin")
    counsellor = _user("COUNSELLOR", "couns")
    receptionist = _user("RECEPTIONIST", "recep")
    admin = _user("ADMIN", "boss")
    today = timezone.localdate()
    a = Patient.objects.create(full_name="Amina", sex="F", date_of_birth=today.replace(year=today.year - 30))
    b = Patient.objects.create(full_name="Baraka", sex="M", estimated_age=3)
    # Last year: Amina seen once (makes her "returning" this year).
    old = Encounter.objects.create(patient=a, clinician=clinician)
    Encounter.objects.filter(pk=old.pk).update(started_at=_at(today - timedelta(days=400)))
    # This period: one visit each, plus a mental-health visit for Baraka.
    e1 = Encounter.objects.create(patient=a, clinician=clinician, status="FINALISED")
    Diagnosis.objects.create(encounter=e1, icd10_code="B54", description="Malaria", is_primary=True)
    Encounter.objects.create(patient=b, clinician=clinician)
    Encounter.objects.create(patient=b, clinician=counsellor, encounter_type="MENTAL_HEALTH")
    inv = Invoice.objects.create(patient=a, encounter=e1, issued_by=receptionist, status="ISSUED")
    InvoiceLine.objects.create(invoice=inv, description="Consultation", quantity=Decimal("1"), unit_price_tzs=Decimal("5000"))
    inv.recalculate()
    Payment.objects.create(invoice=inv, method="MPESA", amount_tzs=Decimal("3000"), received_by=receptionist)
    Payment.objects.create(invoice=inv, method="WAIVER", amount_tzs=Decimal("2000"), received_by=receptionist)
    return {"clinician": clinician, "counsellor": counsellor, "receptionist": receptionist, "admin": admin}


@pytest.mark.django_db
@pytest.mark.parametrize("period", ["day", "week", "month", "quarter", "year"])
def test_reports_render_for_every_period(client, setup_data, period):
    client.force_login(setup_data["admin"])
    resp = client.get(reverse("reports_section:index") + f"?period={period}")
    assert resp.status_code == 200
    assert resp.context["period"].kind == period


@pytest.mark.django_db
def test_report_figures(client, setup_data):
    client.force_login(setup_data["admin"])
    resp = client.get(reverse("reports_section:index") + "?period=year")
    clinical, financial = resp.context["clinical"], resp.context["financial"]
    assert clinical["visits"] == 3  # admin sees the MH visit too
    assert clinical["unique_patients"] == 2
    assert clinical["returning_patients"] == 1 and clinical["new_patients_seen"] == 1
    assert clinical["top_diagnoses"][0]["description"] == "Malaria"
    bands = {row["band"]: row["total"] for row in clinical["demographics"]["rows"]}
    assert bands["Under 5"] == 1 and bands["18–59"] == 1
    assert financial["collected"] == Decimal("3000")  # waiver is not revenue
    assert financial["waived"] == Decimal("2000")


@pytest.mark.django_db
def test_reports_hide_mental_health_from_clinicians(client, setup_data):
    client.force_login(setup_data["clinician"])
    resp = client.get(reverse("reports_section:index") + "?period=year")
    assert resp.context["clinical"]["visits"] == 2


@pytest.mark.django_db
def test_receptionist_sees_financial_section_only(client, setup_data):
    client.force_login(setup_data["receptionist"])
    resp = client.get(reverse("reports_section:index"))
    assert resp.context["sections"] == ["financial"]
    assert "clinical" not in resp.context or resp.context.get("clinical") is None


@pytest.mark.django_db
def test_reports_forbidden_for_lab(client, setup_data):
    client.force_login(_user("LAB", "labby"))
    assert client.get(reverse("reports_section:index")).status_code == 403


@pytest.mark.django_db
def test_report_csv_export_is_audited(client, setup_data):
    client.force_login(setup_data["admin"])
    resp = client.get(reverse("reports_section:index") + "?period=year&export=csv")
    assert resp.status_code == 200 and resp["Content-Type"].startswith("text/csv")
    body = resp.content.decode("utf-8-sig")
    assert "Top diagnoses" in body and "Malaria" in body
    assert AuditLog.objects.filter(action="EXPORT", actor=setup_data["admin"]).exists()


# ─── Visit register ──────────────────────────────────────────────

@pytest.mark.django_db
def test_visit_register_lists_visits_with_mh_rules(client, setup_data):
    client.force_login(setup_data["clinician"])
    resp = client.get(reverse("reports_section:visit_register") + "?period=year")
    assert resp.status_code == 200
    assert resp.context["total_count"] == 2
    client.force_login(setup_data["counsellor"])
    resp = client.get(reverse("reports_section:visit_register") + "?period=year")
    assert resp.context["total_count"] == 3


@pytest.mark.django_db
def test_visit_register_search_and_csv(client, setup_data):
    client.force_login(setup_data["admin"])
    url = reverse("reports_section:visit_register")
    resp = client.get(url + "?period=year&q=malaria")
    assert [e.patient.full_name for e in resp.context["encounters"]] == ["Amina"]
    resp = client.get(url + "?period=year&q=malaria&export=csv")
    rows = resp.content.decode("utf-8-sig").strip().splitlines()
    assert len(rows) == 2 and "Amina" in rows[1] and "Malaria" in rows[1]


@pytest.mark.django_db
def test_visit_register_forbidden_for_receptionist(client, setup_data):
    client.force_login(setup_data["receptionist"])
    assert client.get(reverse("reports_section:visit_register")).status_code == 403
