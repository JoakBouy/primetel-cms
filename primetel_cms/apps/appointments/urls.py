"""Appointments URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "appointments"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="appointments:queue", permanent=False), name="index"),
    path("queue/", views.queue_view, name="queue"),
    path("new/", views.appointment_new, name="new"),
    path("<uuid:pk>/check-in/", views.check_in, name="check_in"),
    path("<uuid:pk>/check-out/", views.check_out, name="check_out"),
]
