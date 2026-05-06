"""Reports views."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.db.models import Sum, Count, F, Q
from django.db.models.functions import Coalesce

from apps.encounters.models import Encounter, Diagnosis
from apps.billing.models import Payment
from apps.pharmacy.models import Prescription, Drug, StockItem, Dispense
from apps.lab.models import LabOrder

from datetime import timedelta

@login_required
def dashboard(request):
    """Role-scoped dashboard — the main landing page after login."""
    today = timezone.now().date()
    start_of_day = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    seven_days_ago = start_of_day - timedelta(days=6)

    # 1. Patients Seen Today (Encounters created today)
    patients_seen_today = Encounter.objects.filter(started_at__gte=start_of_day).count()

    # 2. Revenue Today
    revenue_today = Payment.objects.filter(received_at__gte=start_of_day).aggregate(Sum('amount_tzs'))['amount_tzs__sum'] or 0

    # 3. Prescriptions Dispensed Today — count distinct prescriptions that had a Dispense recorded today.
    rx_dispensed_today = (
        Dispense.objects.filter(dispensed_at__gte=start_of_day)
        .values('prescription_id').distinct().count()
    )

    # 4. Pending Labs — orders awaiting result entry.
    pending_labs = LabOrder.objects.filter(status__in=['ORDERED', 'COLLECTED']).count()

    # 5. Low Stock Drugs
    low_stock_drugs = Drug.objects.filter(is_active=True).annotate(
        total_stock_db=Coalesce(Sum('stock_items__quantity_on_hand'), 0)
    ).filter(total_stock_db__lte=F('low_stock_threshold'))[:5]

    # 6. Near Expiry Batches (within 90 days)
    ninety_days_from_now = today + timedelta(days=90)
    near_expiry_batches = StockItem.objects.filter(
        expiry_date__lte=ninety_days_from_now, quantity_on_hand__gt=0
    ).select_related('drug').order_by('expiry_date')[:5]

    # 7. Patient Volume Chart (last 7 days)
    chart_labels = []
    chart_data = []
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        start = timezone.make_aware(timezone.datetime.combine(day, timezone.datetime.min.time()))
        end = start + timedelta(days=1)
        count = Encounter.objects.filter(started_at__gte=start, started_at__lt=end).count()
        chart_labels.append(day.strftime("%a"))
        chart_data.append(count)

    # 8. Top Diagnoses (last 7 days). Group by ICD code if present, else by description.
    recent_dx = Diagnosis.objects.filter(encounter__started_at__gte=seven_days_ago)
    top_diagnoses = list(
        recent_dx.exclude(icd10_code="")
        .values('icd10_code', 'description')
        .annotate(n=Count('id'))
        .order_by('-n')[:5]
    )
    if len(top_diagnoses) < 5:
        # Fill with free-text dx (no ICD code).
        free_text = list(
            recent_dx.filter(icd10_code="")
            .values('description')
            .annotate(n=Count('id'))
            .order_by('-n')[: 5 - len(top_diagnoses)]
        )
        for row in free_text:
            top_diagnoses.append({"icd10_code": "", "description": row["description"], "n": row["n"]})
    top_dx_max = max((d["n"] for d in top_diagnoses), default=0)

    context = {
        "page_title": _("Dashboard"),
        "patients_seen_today": patients_seen_today,
        "revenue_today": revenue_today,
        "rx_dispensed_today": rx_dispensed_today,
        "pending_labs": pending_labs,
        "low_stock_drugs": low_stock_drugs,
        "near_expiry_batches": near_expiry_batches,
        "chart_labels": chart_labels,
        "chart_data": chart_data,
        "top_diagnoses": top_diagnoses,
        "top_dx_max": top_dx_max,
    }
    return render(request, "reports/dashboard.html", context)
