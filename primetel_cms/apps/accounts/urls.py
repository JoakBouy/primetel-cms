"""
Primetel CMS — Accounts URLs
"""
from django.urls import path

from . import views

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("set-language/", views.set_language_preference, name="set_language"),
]
