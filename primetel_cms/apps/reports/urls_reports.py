"""Reports URLs — reports section."""
from django.urls import path
from django.views.generic import RedirectView

app_name = "reports_section"
urlpatterns = [
    path("", RedirectView.as_view(url="/dashboard/", permanent=False), name="index"),
]
