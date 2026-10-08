"""Reports URLs — reports section."""
from django.urls import path

from . import report_views

app_name = "reports_section"
urlpatterns = [
    path("", report_views.reports_index, name="index"),
    path("visits/", report_views.visit_register, name="visit_register"),
]
