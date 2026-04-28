"""Idle session timeout middleware tests."""
import time

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(username="idletest", password="pw")


@pytest.mark.django_db
def test_idle_timeout_logs_user_out(client, user, settings, monkeypatch):
    settings.IDLE_SESSION_SECONDS = 60
    client.force_login(user)
    # First request: stamps last_activity.
    resp = client.get(reverse("dashboard"))
    assert resp.status_code == 200

    # Force the stamp into the past.
    s = client.session
    s["_last_activity"] = int(time.time()) - 120
    s.save()

    resp2 = client.get(reverse("dashboard"))
    # We get redirected to login, since session was reset.
    assert resp2.status_code == 302
    assert "/login/" in resp2.headers.get("Location", "")


@pytest.mark.django_db
def test_idle_timeout_does_not_fire_within_window(client, user, settings):
    settings.IDLE_SESSION_SECONDS = 1800
    client.force_login(user)
    resp = client.get(reverse("dashboard"))
    assert resp.status_code == 200
    resp2 = client.get(reverse("dashboard"))
    assert resp2.status_code == 200
