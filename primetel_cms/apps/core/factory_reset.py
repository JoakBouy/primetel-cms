"""
Factory-reset helpers.

Shared between the `factory_reset` management command and the admin-panel
button. Wipes operational + catalogue data; preserves users + roles.

Both entry points call `plan_factory_reset()` to compute counts and
`run_factory_reset()` to actually execute. Run is wrapped in a single
transaction so a mid-wipe error rolls back cleanly.
"""
from __future__ import annotations

from django.apps import apps
from django.conf import settings
from django.db import transaction


# (app_label, ModelName) pairs in delete order. Children before parents.
# Each tuple's history shadow table (if any) is wiped alongside.
DELETE_ORDER = [
    # Notifications + audit
    ("core", "Notification"),
    ("core", "AuditLog"),

    # Pharmacy — dispenses → prescriptions → stock movements → stock items → drugs
    ("pharmacy", "Dispense"),
    ("pharmacy", "Prescription"),
    ("pharmacy", "StockMovement"),
    ("pharmacy", "StockItem"),
    ("pharmacy", "Drug"),

    # Lab — results → orders → tests
    ("lab", "LabResult"),
    ("lab", "LabOrder"),
    ("lab", "LabTest"),

    # Billing — payments → lines → invoices → service items
    ("billing", "Payment"),
    ("billing", "InvoiceLine"),
    ("billing", "Invoice"),
    ("billing", "ServiceItem"),

    # Encounters — children → parents
    ("encounters", "EncounterAttachment"),
    ("encounters", "MentalHealthAssessment"),
    ("encounters", "Diagnosis"),
    ("encounters", "Vitals"),
    ("encounters", "Encounter"),
    ("encounters", "ICD10Code"),

    # Appointments
    ("appointments", "Appointment"),
    ("appointments", "AppointmentType"),

    # Patients last — they're referenced by almost everything above
    ("patients", "Patient"),

    # Core lookup data
    ("core", "ReasonCode"),
]


def _history_model_for(model):
    """Return the django-simple-history shadow model for `model`, or None."""
    history_model_name = f"Historical{model.__name__}"
    try:
        return apps.get_model(model._meta.app_label, history_model_name)
    except LookupError:
        return None


def plan_factory_reset():
    """Return a list of (app_label, model_name, live_count, history_count)
    tuples and the totals for live + history. Read-only — never deletes."""
    plan = []
    live_total = 0
    history_total = 0
    for app_label, model_name in DELETE_ORDER:
        try:
            model = apps.get_model(app_label, model_name)
        except LookupError:
            continue
        live = model.objects.count()
        live_total += live
        history_model = _history_model_for(model)
        history = history_model.objects.count() if history_model else 0
        history_total += history
        plan.append((app_label, model_name, live, history))

    from django.contrib.auth import get_user_model
    from apps.accounts.models import Role
    kept = {
        "users": get_user_model().objects.count(),
        "roles": Role.objects.count(),
    }
    return plan, live_total, history_total, kept


def run_factory_reset():
    """Execute the wipe atomically. Returns a summary list of
    (label, live_deleted, history_deleted) tuples."""
    summary = []
    with transaction.atomic():
        for app_label, model_name in DELETE_ORDER:
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError:
                continue
            history_model = _history_model_for(model)
            h_deleted = 0
            if history_model is not None:
                h_deleted, _ = history_model.objects.all().delete()
            live_deleted, _ = model.objects.all().delete()
            summary.append((f"{app_label}.{model_name}", live_deleted, h_deleted))
    return summary


_PROD_HOST_SIGNALS = ("onrender.com", "primetel.tech")


def looks_like_production() -> bool:
    """Best-effort guess based on ALLOWED_HOSTS."""
    hosts = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    return any(any(p in str(h).lower() for p in _PROD_HOST_SIGNALS) for h in hosts)
