"""Primetel CMS — Pharmacy Views."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext_lazy as _
from apps.accounts.decorators import requires_role
from .models import Prescription, Drug, StockItem


@requires_role("PHARMACY", "ADMIN")
def rx_queue(request):
    """Prescription queue — shows all PRESCRIBED items awaiting dispensing."""
    pending = Prescription.objects.filter(status="PRESCRIBED").select_related("encounter__patient", "drug").order_by("prescribed_at")
    return render(request, "pharmacy/rx_queue.html", {
        "page_title": _("Rx Queue"),
        "prescriptions": pending,
    })


@requires_role("PHARMACY", "ADMIN")
def stock_list(request):
    """Drug stock overview."""
    drugs = Drug.objects.filter(is_active=True).prefetch_related("stock_items")
    return render(request, "pharmacy/stock.html", {
        "page_title": _("Stock Management"),
        "drugs": drugs,
    })


@requires_role("PHARMACY", "ADMIN")
def drug_detail(request, pk):
    """Single drug stock detail."""
    drug = get_object_or_404(Drug, pk=pk)
    batches = drug.stock_items.all()
    return render(request, "pharmacy/drug_detail.html", {
        "page_title": drug.generic_name,
        "drug": drug,
        "batches": batches,
    })
