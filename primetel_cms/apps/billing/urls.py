"""Billing URLs."""
from django.urls import path
from . import views

app_name = "billing"

urlpatterns = [
    path("invoices/", views.invoice_list, name="invoice_list"),
    path("invoices/<uuid:pk>/", views.invoice_detail, name="invoice_detail"),
]
