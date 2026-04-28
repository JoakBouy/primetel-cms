"""
Primetel CMS — Accounts Views
"""
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import gettext_lazy as _

from .forms import LoginForm

# Django 5.x removed translation.LANGUAGE_SESSION_KEY — use the settings constant
LANGUAGE_SESSION_KEY = getattr(
    translation, "LANGUAGE_SESSION_KEY", settings.LANGUAGE_COOKIE_NAME
)


def login_view(request):
    """Custom login view with premium UI."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = LoginForm(request, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)

        # Set language preference
        if hasattr(user, "language_preference") and user.language_preference:
            translation.activate(user.language_preference)
            request.session["_language"] = user.language_preference

        return redirect("dashboard")

    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    """Logout and redirect to login."""
    logout(request)
    return redirect("login")


@login_required
def set_language_preference(request):
    """Update user's language preference and activate it."""
    if request.method == "POST":
        lang = request.POST.get("language", "sw")
        if lang in ("sw", "en"):
            request.user.language_preference = lang
            request.user.save(update_fields=["language_preference"])
            translation.activate(lang)
            request.session["_language"] = lang
    # Redirect back to referrer or dashboard
    next_url = request.META.get("HTTP_REFERER", "/dashboard/")
    return redirect(next_url)
