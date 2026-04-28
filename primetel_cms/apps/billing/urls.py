"""Billing URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "billing"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="billing:invoice_list", permanent=False), name="index"),
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<uuid:pk>/", views.invoice_detail, name="invoice_detail"),
]
