"""
Primetel CMS — Accounts Views
"""
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.utils import translation

from .forms import LoginForm


def _activate_language(request, response, lang):
    """Activate a language for this request and persist it on the response.

    Sets the LANGUAGE_COOKIE_NAME cookie that Django's LocaleMiddleware reads
    on every subsequent request. Without this cookie, toggling language has
    no effect because LocaleMiddleware never sees the choice.
    """
    if lang not in dict(settings.LANGUAGES):
        return response
    translation.activate(lang)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        lang,
        max_age=getattr(settings, "LANGUAGE_COOKIE_AGE", None),
        path=settings.LANGUAGE_COOKIE_PATH,
        domain=settings.LANGUAGE_COOKIE_DOMAIN,
        secure=settings.LANGUAGE_COOKIE_SECURE,
        httponly=settings.LANGUAGE_COOKIE_HTTPONLY,
        samesite=settings.LANGUAGE_COOKIE_SAMESITE,
    )
    return response


def login_view(request):
    """Custom login view with premium UI."""
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = LoginForm(request, data=request.POST or None)

    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        login(request, user)
        response = redirect("dashboard")
        # Apply user's saved language preference on login.
        if getattr(user, "language_preference", None):
            response = _activate_language(request, response, user.language_preference)
        return response

    return render(request, "accounts/login.html", {"form": form})


def logout_view(request):
    """Logout and redirect to login."""
    logout(request)
    return redirect("login")


@login_required
def set_language_preference(request):
    """Switch UI language and remember the choice on the user record."""
    lang = request.POST.get("language", "") if request.method == "POST" else ""
    next_url = request.META.get("HTTP_REFERER") or "/dashboard/"
    response = HttpResponseRedirect(next_url)
    if lang in dict(settings.LANGUAGES):
        # Persist to user so future logins land in their preferred language.
        if getattr(request.user, "language_preference", None) != lang:
            request.user.language_preference = lang
            request.user.save(update_fields=["language_preference"])
        response = _activate_language(request, response, lang)
    return response
