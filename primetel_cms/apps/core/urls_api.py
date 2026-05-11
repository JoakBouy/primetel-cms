"""Core API URLs — HTMX endpoints (notifications, etc.)."""
from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.notifications_panel, name="notifications_panel"),
    path("notifications/badge/", views.notifications_badge, name="notifications_badge"),
    path("notifications/<uuid:pk>/read/", views.notifications_mark_read, name="notifications_mark_read"),
    path("notifications/read-all/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("my-activity/", views.my_activity, name="my_activity"),
]
