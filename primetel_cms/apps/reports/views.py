"""Reports views."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from django.db.models import Sum, Count, F
from django.db.models.functions import Coalesce

from apps.encounters.models import Encounter, Diagnosis
from apps.billing.models import Payment
from apps.pharmacy.models import Prescription, Drug, StockItem
from apps.lab.models import LabOrder

from datetime import timedelta

@login_required
def dashboard(request):
    """Role-scoped dashboard — the main landing page after login."""
    today = timezone.now().date()
    start_of_day = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    
    # 1. Patients Seen Today (Encounters created today)
    patients_seen_today = Encounter.objects.filter(started_at__gte=start_of_day).count()
    
    # 2. Revenue Today
    revenue_today = Payment.objects.filter(received_at__gte=start_of_day).aggregate(Sum('amount_tzs'))['amount_tzs__sum'] or 0
    
    # 3. Prescriptions Dispensed Today
    rx_dispensed_today = Prescription.objects.filter(status='DISPENSED', updated_at__gte=start_of_day).count()
    
    # 4. Pending Labs
    pending_labs = LabOrder.objects.filter(status__in=['ORDERED', 'SAMPLE_COLLECTED']).count()
    
    # 5. Low Stock Drugs
    low_stock_drugs = Drug.objects.annotate(
        total_stock_db=Coalesce(Sum('stock_items__quantity_on_hand'), 0)
    ).filter(total_stock_db__lte=F('low_stock_threshold'))[:5]
    
    # 6. Near Expiry Batches (within 90 days)
    ninety_days_from_now = today + timedelta(days=90)
    near_expiry_batches = StockItem.objects.filter(expiry_date__lte=ninety_days_from_now, quantity_on_hand__gt=0).order_by('expiry_date')[:5]
    
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
    }
    return render(request, "reports/dashboard.html", context)
