"""Billing URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "billing"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="billing:invoice_list", permanent=False), name="index"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/new/", views.invoice_new, name="invoice_new"),
    path("invoices/<uuid:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<uuid:pk>/lines/", views.invoice_add_line, name="invoice_add_line"),
    path("invoices/<uuid:pk>/lines/<uuid:line_pk>/remove/", views.invoice_remove_line, name="invoice_remove_line"),
    path("invoices/<uuid:pk>/issue/", views.invoice_issue, name="invoice_issue"),
    path("invoices/<uuid:pk>/payments/", views.payment_record, name="payment_record"),
    path("invoices/<uuid:pk>/payments/<uuid:payment_pk>/void/", views.payment_void, name="payment_void"),
    path("invoices/<uuid:pk>/receipt/", views.receipt_print, name="receipt_print"),
]
