import os
import secrets
import subprocess
import sys
from uuid import UUID

import pytest
from django.contrib.auth import SESSION_KEY
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from tests.error_contract import error_content


@pytest.mark.parametrize(
    ("key", "kind"),
    [
        ("DJANGO_SECRET_KEY", "missing"),
        ("JWT_SIGNING_KEY", "missing"),
        ("DJANGO_SECRET_KEY", "empty"),
        ("JWT_SIGNING_KEY", "empty"),
        ("DJANGO_SECRET_KEY", "short"),
        ("JWT_SIGNING_KEY", "short"),
        ("DJANGO_SECRET_KEY", "placeholder"),
        ("JWT_SIGNING_KEY", "placeholder"),
        ("DJANGO_SECRET_KEY", "repeated"),
        ("JWT_SIGNING_KEY", "repeated"),
        ("JWT_SIGNING_KEY", "same"),
        (None, "valid"),
    ],
)
def test_base_settings_require_independent_non_placeholder_keys(key, kind):
    django_key = secrets.token_urlsafe(64)
    jwt_key = secrets.token_urlsafe(64)
    values = {
        "missing": None,
        "empty": "",
        "short": "Ab1!2",
        "placeholder": f"replace-with-{jwt_key}",
        "repeated": "y" * 64,
        "same": django_key,
    }
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.base",
        "DJANGO_SECRET_KEY": django_key,
        "JWT_SIGNING_KEY": jwt_key,
        "DATABASE_URL": "postgresql://localhost/stage22_config_only",
        "DJANGO_ALLOWED_HOSTS": "security-check.invalid",
        "DJANGO_DEBUG": "True",
        "DJANGO_SESSION_COOKIE_SECURE": "True",
        "DJANGO_CSRF_COOKIE_SECURE": "True",
        "DJANGO_SECURE_SSL_REDIRECT": "True",
        "DJANGO_SECURE_HSTS_SECONDS": "600",
    }
    if kind == "missing":
        del env[key]
    elif key:
        env[key] = values[kind]

    script = """
import environ
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

environ.Env.read_env = lambda *args, **kwargs: None
try:
    assert settings.DEBUG is False
    assert settings.ALLOWED_HOSTS == ["security-check.invalid"]
    assert settings.SECURE_SSL_REDIRECT
    assert settings.SESSION_COOKIE_SECURE
    assert settings.CSRF_COOKIE_SECURE
    assert settings.SECURE_HSTS_SECONDS == 600
    assert settings.SECURE_PROXY_SSL_HEADER is None
    assert not settings.USE_X_FORWARDED_HOST
except ImproperlyConfigured as exc:
    print(str(exc))
    raise SystemExit(2)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.stderr == ""
    assert all(value not in result.stdout for value in (django_key, jwt_key))
    if kind == "valid":
        assert result.returncode == 0
        assert result.stdout == ""
    else:
        assert result.returncode == 2
        assert key in result.stdout
        candidate = values[kind]
        if candidate:
            assert candidate not in result.stdout


def test_host_validation_preserves_safe_api_error(settings):
    settings.DEBUG = False
    settings.SECURE_SSL_REDIRECT = False
    settings.ALLOWED_HOSTS = ["testserver"]
    client = APIClient()
    path = reverse("accounts:me")

    denied = client.get(path, HTTP_HOST="unlisted.example")
    assert denied.status_code == 400
    assert denied["Content-Type"] == "application/json"
    assert error_content(denied) == {
        "code": "BAD_REQUEST",
        "message": "Bad request.",
        "details": {},
    }
    allowed = client.get(path, HTTP_HOST="testserver")
    assert allowed.status_code == 401
    assert error_content(allowed)["code"] == "not_authenticated"


def test_https_redirect_and_security_headers(settings):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SECURE_SSL_REDIRECT = True
    settings.SECURE_HSTS_SECONDS = 600
    client = APIClient()
    path = reverse("accounts:me")

    redirect = client.get(path, HTTP_X_FORWARDED_PROTO="https")
    assert redirect.status_code == 301
    assert redirect["Location"] == f"https://testserver{path}"
    assert str(UUID(redirect["X-Request-ID"])) == redirect["X-Request-ID"]
    assert "Strict-Transport-Security" not in redirect

    secure = client.get(path, secure=True)
    assert secure.status_code == 401
    assert error_content(secure)["code"] == "not_authenticated"
    assert secure["X-Content-Type-Options"] == "nosniff"
    assert secure["X-Frame-Options"] == "DENY"
    assert secure["Referrer-Policy"] == "same-origin"
    assert secure["Strict-Transport-Security"] == "max-age=600"


def test_admin_requires_csrf_and_jwt_api_requires_bearer(
    settings, manager, raw_password
):
    settings.DEBUG = False
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SESSION_COOKIE_SECURE = True
    settings.CSRF_COOKIE_SECURE = True
    manager.is_staff = True
    manager.save(update_fields=["is_staff"])
    client = APIClient(enforce_csrf_checks=True)
    admin = reverse("admin:login")
    credentials = {"username": manager.email, "password": raw_password}
    referer = f"https://testserver{admin}"

    login_form = client.get(admin, secure=True)
    assert login_form.status_code == 200
    csrf = login_form.cookies[settings.CSRF_COOKIE_NAME]
    assert csrf["secure"]
    assert csrf["samesite"] == "Lax"
    denied = client.post(admin, credentials, secure=True, HTTP_REFERER=referer)
    assert denied.status_code == 403
    assert SESSION_KEY not in client.session

    allowed = client.post(
        admin,
        {**credentials, "csrfmiddlewaretoken": csrf.value},
        secure=True,
        HTTP_REFERER=referer,
    )
    assert allowed.status_code == 302
    session = allowed.cookies[settings.SESSION_COOKIE_NAME]
    assert session["secure"] and session["httponly"]
    assert session["samesite"] == "Lax"
    assert allowed.cookies[settings.CSRF_COOKIE_NAME]["secure"]
    assert client.session[SESSION_KEY] == str(manager.pk)

    profile = reverse("accounts:me")
    payload = {"first_name": "Stage 22"}
    session_only = client.patch(profile, payload, secure=True, format="json")
    assert session_only.status_code == 401
    assert error_content(session_only)["code"] == "not_authenticated"
    manager.refresh_from_db()
    assert manager.first_name != payload["first_name"]

    bearer = APIClient(enforce_csrf_checks=True).patch(
        profile,
        payload,
        secure=True,
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(manager)}",
    )
    assert bearer.status_code == 200
    manager.refresh_from_db()
    assert manager.first_name == payload["first_name"]
