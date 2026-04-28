"""Primetel CMS — Billing Views."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils.translation import gettext_lazy as _
from apps.accounts.decorators import requires_role
from .models import Invoice, Payment


@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_list(request):
    """Invoice list — filterable by status."""
    status = request.GET.get("status", "")
    qs = Invoice.objects.select_related("patient").order_by("-issued_at")
    if status:
        qs = qs.filter(status=status)
    return render(request, "billing/invoices.html", {
        "page_title": _("Invoices"),
        "invoices": qs[:100],
        "current_status": status,
    })


@requires_role("RECEPTIONIST", "FINANCE", "ADMIN")
def invoice_detail(request, pk):
    """Invoice detail with lines and payments."""
    invoice = get_object_or_404(Invoice.objects.select_related("patient"), pk=pk)
    return render(request, "billing/invoice_detail.html", {
        "page_title": invoice.invoice_number,
        "invoice": invoice,
        "lines": invoice.lines.all(),
        "payments": invoice.payments.all(),
    })
