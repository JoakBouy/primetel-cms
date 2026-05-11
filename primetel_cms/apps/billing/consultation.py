"""
Consultation auto-charge.

When a clinician opens an encounter, we generate a draft invoice with the
right consultation line so the patient can be billed without a separate
manual step.

Pricing rule (set by clinic admin):

- New patient (no prior FINALISED encounters): 5000 TZS — code CONS-NEW
- Follow-up:                                    3000 TZS — code CONS-FU
- First antenatal consultation:                 5000 TZS — code CONS-ANC
- Mental-health consultation:                  10000 TZS — code CONS-MH

If the matching ServiceItem doesn't exist (catalogue not seeded), the
function falls back to creating an invoice line with the description and
hard-coded price, so a missing catalogue doesn't block clinical work.
"""
from __future__ import annotations

from decimal import Decimal


# Default fallback prices in case the ServiceItem table isn't seeded.
DEFAULTS = {
    "CONS-NEW": ("New patient consultation", Decimal("5000")),
    "CONS-FU":  ("Follow-up consultation",   Decimal("3000")),
    "CONS-ANC": ("First antenatal consultation", Decimal("5000")),
    "CONS-MH":  ("Mental health consultation", Decimal("10000")),
}


def _is_new_patient(patient) -> bool:
    """A patient is 'new' if they have no FINALISED encounters yet."""
    return not patient.encounters.filter(status="FINALISED").exists()


def _is_first_antenatal(encounter) -> bool:
    """The first FINALISED antenatal visit is priced like a new consultation."""
    return not encounter.patient.encounters.filter(
        encounter_type="ANC",
        status="FINALISED",
    ).exists()


def consultation_code(encounter) -> str:
    """Pick the right service code for this encounter."""
    if encounter.encounter_type == "MENTAL_HEALTH":
        return "CONS-MH"
    if encounter.encounter_type == "ANC" and _is_first_antenatal(encounter):
        return "CONS-ANC"
    return "CONS-NEW" if _is_new_patient(encounter.patient) else "CONS-FU"


def auto_charge(encounter, user) -> "Invoice | None":  # noqa: F821
    """
    Create a draft invoice for the consultation.

    Returns the Invoice (or None if creation failed). Idempotent at the
    encounter level: an encounter only generates one auto-charge invoice;
    if one already exists, it's returned unchanged.
    """
    # Local imports to avoid circular dependency at app load.
    from apps.billing.models import Invoice, InvoiceLine, ServiceItem

    existing = Invoice.objects.filter(encounter=encounter).first()
    if existing is not None:
        return existing

    code = consultation_code(encounter)
    desc, price = DEFAULTS[code]
    service = ServiceItem.objects.filter(code=code, is_active=True).first()
    if service is not None:
        desc = service.name
        price = service.unit_price_tzs

    invoice = Invoice.objects.create(
        patient=encounter.patient,
        encounter=encounter,
        issued_by=user,
        created_by=user,
    )
    InvoiceLine.objects.create(
        invoice=invoice,
        service_item=service,
        description=desc,
        quantity=Decimal("1"),
        unit_price_tzs=price,
    )
    invoice.recalculate()
    return invoice
