"""Pharmacy URLs."""
from django.urls import path
from . import views

app_name = "pharmacy"

urlpatterns = [
    path("queue/", views.rx_queue, name="rx_queue"),
    path("stock/", views.stock_list, name="stock"),
    path("drugs/<uuid:pk>/", views.drug_detail, name="drug_detail"),
]
