"""
Billing side-effects of clinical actions.

Prescriptions and lab orders are billed onto the encounter's invoice when they
are created. Each billed line remembers its source, so cancelling, voiding or
editing the prescription/order adjusts the charge instead of leaving the
patient owing for something they never received.
"""
from __future__ import annotations

import logging
from decimal import Decimal

from django.db import transaction

from .models import Invoice, InvoiceLine

logger = logging.getLogger(__name__)


# ─── Descriptions ───────────────────────────────────────────────

def prescription_line_description(drug) -> str:
    return f"{drug.generic_name} {drug.strength} ({drug.get_form_display()})"


def lab_line_description(test) -> str:
    return f"{test.code} {test.name}"


# ─── Charging ───────────────────────────────────────────────────

def _encounter_invoice(encounter_id):
    return Invoice.objects.filter(encounter_id=encounter_id).first()


def bill_prescription(rx) -> bool:
    """Add a line for `rx` to its encounter invoice. Returns True if billed.

    No invoice (legacy encounters) or a closed invoice → nothing is billed.
    Never raises: a billing hiccup must not block prescribing.
    """
    try:
        invoice = _encounter_invoice(rx.encounter_id)
        if invoice is None or invoice.is_closed:
            return False
        InvoiceLine.objects.create(
            invoice=invoice,
            prescription=rx,
            description=prescription_line_description(rx.drug),
            quantity=Decimal(rx.quantity),
            unit_price_tzs=rx.drug.unit_price_tzs or Decimal("0"),
        )
        invoice.recalculate()
        return True
    except Exception:
        logger.exception("Could not bill prescription %s", rx.pk)
        return False


def bill_lab_order(order):
    """Add a line for `order` to its encounter invoice. Returns the invoice or None."""
    try:
        invoice = _encounter_invoice(order.encounter_id)
        if invoice is None or invoice.is_closed:
            return None
        InvoiceLine.objects.create(
            invoice=invoice,
            lab_order=order,
            description=lab_line_description(order.test),
            quantity=Decimal("1"),
            unit_price_tzs=order.test.price_tzs or Decimal("0"),
        )
        invoice.recalculate()
        return invoice
    except Exception:
        logger.exception("Could not bill lab order %s", order.pk)
        return None


# ─── Reversing / adjusting charges ──────────────────────────────

def _prescription_lines(rx, invoice):
    lines = list(invoice.lines.filter(prescription=rx))
    if lines:
        return lines
    # Lines billed before they were linked to their prescription: match the
    # first unlinked line with the same description and quantity.
    legacy = (
        invoice.lines.filter(
            prescription__isnull=True,
            lab_order__isnull=True,
            description=prescription_line_description(rx.drug),
            quantity=Decimal(rx.quantity),
        )
        .order_by("pk")
        .first()
    )
    return [legacy] if legacy else []


def _lab_lines(order, invoice):
    lines = list(invoice.lines.filter(lab_order=order))
    if lines:
        return lines
    legacy = (
        invoice.lines.filter(
            prescription__isnull=True,
            lab_order__isnull=True,
            description=lab_line_description(order.test),
        )
        .order_by("pk")
        .first()
    )
    return [legacy] if legacy else []


def _remove(encounter_id, find_lines):
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().filter(encounter_id=encounter_id).first()
        if invoice is None or invoice.is_closed:
            return None
        lines = find_lines(invoice)
        if not lines:
            return None
        for line in lines:
            line.delete()
        invoice.recalculate()
        return invoice


def remove_prescription_charge(rx):
    """Remove the charge for a cancelled/voided prescription.

    Returns the updated invoice, or None if nothing was removed. A negative
    balance on the returned invoice means the patient is owed a refund.
    """
    return _remove(rx.encounter_id, lambda inv: _prescription_lines(rx, inv))


def remove_lab_charge(order):
    """Remove the charge for a cancelled lab order. Same contract as above."""
    return _remove(order.encounter_id, lambda inv: _lab_lines(order, inv))


def update_prescription_charge(rx, *, previous_drug, previous_quantity):
    """Re-price the line for an edited prescription. Returns the invoice or None."""
    with transaction.atomic():
        invoice = Invoice.objects.select_for_update().filter(encounter_id=rx.encounter_id).first()
        if invoice is None or invoice.is_closed:
            return None
        lines = list(invoice.lines.filter(prescription=rx))
        if not lines:
            legacy = (
                invoice.lines.filter(
                    prescription__isnull=True,
                    lab_order__isnull=True,
                    description=prescription_line_description(previous_drug),
                    quantity=Decimal(previous_quantity),
                )
                .order_by("pk")
                .first()
            )
            lines = [legacy] if legacy else []
        if not lines:
            return None
        line, extra = lines[0], lines[1:]
        line.prescription = rx
        line.description = prescription_line_description(rx.drug)
        line.quantity = Decimal(rx.quantity)
        line.unit_price_tzs = rx.drug.unit_price_tzs or Decimal("0")
        line.save()
        for dup in extra:
            dup.delete()
        invoice.recalculate()
        return invoice


# ─── Prepaid consultations ──────────────────────────────────────

def prepay_kind_for_lines(invoice) -> str:
    """Infer the prepay kind of an encounter-less invoice from its lines."""
    lines = invoice.lines.select_related("service_item")
    kind = ""
    for line in lines:
        item = line.service_item
        text = line.description.lower()
        # "[Prepay] …" lines come from Send-to-billing; a hand-typed custom
        # line mentioning a consultation counts too.
        is_consult_text = line.description.startswith("[Prepay]") or "consult" in text
        if (item is not None and item.code == "CONS-MH") or (is_consult_text and "mental" in text):
            return "MENTAL_HEALTH"
        if (item is not None and item.category == "CONSULT") or is_consult_text:
            kind = "GENERAL"
    return kind


def refresh_prepay_type(invoice):
    """Keep `prepay_type` in step with the lines of a front-desk invoice.

    Only fills in a missing kind (reception added a consultation line) or
    clears it (the consultation line was removed); an explicit kind set by
    prepay_consultation is otherwise left alone.
    """
    if invoice.encounter_id:
        return
    kind = prepay_kind_for_lines(invoice)
    if (not invoice.prepay_type and kind) or (invoice.prepay_type and not kind):
        invoice.prepay_type = kind
        invoice.save(update_fields=["prepay_type"])
