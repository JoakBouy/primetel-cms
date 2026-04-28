"""Lab URLs."""
from django.urls import path
from . import views

app_name = "lab"

urlpatterns = [
    path("queue/", views.lab_queue, name="queue"),
    path("orders/<uuid:pk>/", views.lab_order_detail, name="order_detail"),
]
