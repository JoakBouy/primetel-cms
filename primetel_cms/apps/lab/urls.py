"""Lab URLs."""
from django.urls import path
from django.views.generic import RedirectView
from . import views

app_name = "lab"

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="lab:queue", permanent=False), name="index"),
    path("queue/", views.lab_queue, name="queue"),
    path("orders/<uuid:pk>/", views.lab_order_detail, name="order_detail"),
]
