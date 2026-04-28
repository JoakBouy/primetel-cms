"""Patients URLs."""
from django.urls import path

from . import views

app_name = "patients"

urlpatterns = [
    path("", views.patient_list, name="list"),
    path("new/", views.patient_register, name="register"),
    path("<uuid:pk>/", views.patient_chart, name="chart"),
    path("<uuid:pk>/edit/", views.patient_edit, name="edit"),
    path("<uuid:pk>/flag/", views.patient_flag, name="flag"),
    path("<uuid:pk>/photo/", views.patient_photo, name="photo"),
]
