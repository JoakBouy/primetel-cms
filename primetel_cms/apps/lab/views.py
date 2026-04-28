"""Primetel CMS — Lab Views."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from apps.accounts.decorators import requires_role
from .models import LabOrder


@requires_role("LAB", "CLINICIAN", "ADMIN")
def lab_queue(request):
    """Lab queue — tests awaiting processing."""
    pending = LabOrder.objects.filter(status__in=["ORDERED", "COLLECTED"]).select_related("encounter__patient", "test").order_by("ordered_at")
    return render(request, "lab/queue.html", {
        "page_title": _("Lab Queue"),
        "orders": pending,
    })


@requires_role("LAB", "CLINICIAN", "ADMIN")
def lab_order_detail(request, pk):
    """Lab order detail / result entry."""
    order = get_object_or_404(LabOrder.objects.select_related("test", "encounter__patient"), pk=pk)
    return render(request, "lab/order_detail.html", {
        "page_title": f"{order.test.name}",
        "order": order,
    })
