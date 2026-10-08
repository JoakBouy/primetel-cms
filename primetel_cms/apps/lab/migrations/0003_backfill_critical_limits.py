"""Fill in critical (panic) limits for the seeded lab tests.

Only touches a test when both limits are still empty and its unit matches the
seeded unit, so a lab that changed units or set its own limits is left alone.
"""
from decimal import Decimal

from django.db import migrations

# code: (unit, critical_low, critical_high)
CRITICAL_LIMITS = {
    "HGB": ("g/dL", "7.0", "20.0"),
    "WBC": ("x10^9/L", "2.0", "30.0"),
    "PLT": ("x10^9/L", "50", "1000"),
    "RBS": ("mg/dL", "50", "400"),
    "FBS": ("mg/dL", "50", "400"),
    "CREAT": ("mg/dL", None, "5.0"),
}


def forwards(apps, schema_editor):
    LabTest = apps.get_model("lab", "LabTest")
    for code, (unit, low, high) in CRITICAL_LIMITS.items():
        LabTest.objects.filter(
            code=code,
            reference_unit=unit,
            critical_low__isnull=True,
            critical_high__isnull=True,
        ).update(
            critical_low=Decimal(low) if low else None,
            critical_high=Decimal(high) if high else None,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("lab", "0002_labtest_critical_limits"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
