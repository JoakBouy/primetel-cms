"""Pharmacy URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "pharmacy"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="pharmacy:rx_queue", permanent=False), name="index"),
    path("queue/", views.rx_queue, name="rx_queue"),
    path("stock/", views.stock_list, name="stock"),
    path("drugs/<uuid:pk>/", views.drug_detail, name="drug_detail"),
]
