"""Backfill data for the prepay/reversal fields added in 0003.

1. prepay_type: invoices with no encounter that carry a consultation line
   (a CONSULT service item, a "[Prepay] …" line from Send-to-billing, or a
   custom line mentioning a consultation) are prepaid consultations. CONS-MH / mental-health lines → MENTAL_HEALTH,
   anything else → GENERAL. Other front-desk invoices stay unmarked.
2. reverses: voids used to be recorded as a negative payment with reference
   "VOID of <payment id>: <reason>". Link them to the original so those
   payments can't be voided a second time.
"""
import re
import uuid

from django.db import migrations

_VOID_REF = re.compile(r"^VOID of ([0-9a-fA-F-]{36})")


def _prepay_kind(lines):
    kind = ""
    for line in lines:
        item = line.service_item
        text = line.description.lower()
        is_consult_text = line.description.startswith("[Prepay]") or "consult" in text
        if (item is not None and item.code == "CONS-MH") or (is_consult_text and "mental" in text):
            return "MENTAL_HEALTH"
        if (item is not None and item.category == "CONSULT") or is_consult_text:
            kind = "GENERAL"
    return kind


def forwards(apps, schema_editor):
    Invoice = apps.get_model("billing", "Invoice")
    Payment = apps.get_model("billing", "Payment")

    for invoice in Invoice.objects.filter(encounter__isnull=True, prepay_type=""):
        kind = _prepay_kind(invoice.lines.select_related("service_item"))
        if kind:
            Invoice.objects.filter(pk=invoice.pk).update(prepay_type=kind)

    for reversal in Payment.objects.filter(amount_tzs__lt=0, reverses__isnull=True):
        match = _VOID_REF.match(reversal.reference or "")
        if not match:
            continue
        try:
            original_pk = uuid.UUID(match.group(1))
        except ValueError:
            continue
        if Payment.objects.filter(pk=original_pk, invoice_id=reversal.invoice_id).exists():
            Payment.objects.filter(pk=reversal.pk).update(reverses_id=original_pk)


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_prepay_type_line_sources_payment_reversals"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
