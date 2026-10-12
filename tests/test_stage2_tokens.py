from datetime import timedelta

import pytest
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken


pytestmark = pytest.mark.django_db


def test_login_returns_access_and_refresh(api_client, customer, raw_password):
    response = api_client.post(
        reverse("accounts:token"),
        {"email": customer.email.upper(), "password": raw_password},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    access = AccessToken(body["access"])
    refresh = RefreshToken(body["refresh"])
    assert str(access["user_id"]) == str(customer.pk)
    assert str(refresh["user_id"]) == str(customer.pk)
    assert access["token_type"] == "access"
    assert refresh["token_type"] == "refresh"


def test_login_rejects_wrong_password(api_client, customer):
    response = api_client.post(
        reverse("accounts:token"),
        {"email": customer.email, "password": "wrong-password"},
        format="json",
    )

    assert response.status_code == 401
    assert "access" not in response.json()


def test_login_rejects_missing_account(api_client, raw_password):
    response = api_client.post(
        reverse("accounts:token"),
        {"email": "missing@example.com", "password": raw_password},
        format="json",
    )

    assert response.status_code == 401


def test_login_rejects_inactive_account(api_client, customer, raw_password):
    customer.is_active = False
    customer.save(update_fields=["is_active", "updated_at"])

    response = api_client.post(
        reverse("accounts:token"),
        {"email": customer.email, "password": raw_password},
        format="json",
    )

    assert response.status_code == 401


def test_login_preserves_password_whitespace(api_client, customer):
    password = "  Zp9!R6m4#T2q8A  "
    customer.set_password(password)
    customer.save(update_fields=["password", "updated_at"])

    response = api_client.post(
        reverse("accounts:token"),
        {"email": customer.email, "password": password},
        format="json",
    )

    assert response.status_code == 200


def test_refresh_rotates_pair_and_rejects_old_refresh(api_client, tokens):
    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["refresh"]},
        format="json",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["refresh"] != tokens["refresh"]
    assert body["access"] != tokens["access"]
    AccessToken(body["access"])
    RefreshToken(body["refresh"])

    repeated = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert repeated.status_code == 401


def test_refresh_does_not_require_valid_access_header(api_client, tokens):
    api_client.credentials(HTTP_AUTHORIZATION="Bearer invalid-access")

    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["refresh"]},
        format="json",
    )

    assert response.status_code == 200


def test_refresh_rejects_access_token(api_client, tokens):
    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["access"]},
        format="json",
    )

    assert response.status_code == 401


def test_refresh_rejects_malformed_token(api_client):
    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": "not-a-jwt"},
        format="json",
    )

    assert response.status_code == 401


def test_refresh_rejects_expired_token(api_client, customer):
    refresh = RefreshToken.for_user(customer)
    refresh.set_exp(lifetime=timedelta(seconds=-1))

    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": str(refresh)},
        format="json",
    )

    assert response.status_code == 401


def test_refresh_rejects_inactive_user(api_client, customer, tokens):
    customer.is_active = False
    customer.save(update_fields=["is_active", "updated_at"])

    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["refresh"]},
        format="json",
    )

    assert response.status_code == 401


def test_refresh_handles_deleted_user(api_client, customer, tokens):
    customer.delete()

    response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["refresh"]},
        format="json",
    )

    assert response.status_code == 401


def test_expired_access_cannot_read_profile(api_client, customer):
    access = AccessToken.for_user(customer)
    access.set_exp(lifetime=timedelta(seconds=-1))
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    response = api_client.get(reverse("accounts:me"))

    assert response.status_code == 401


def test_refresh_cannot_be_used_as_access(api_client, tokens):
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['refresh']}")

    response = api_client.get(reverse("accounts:me"))

    assert response.status_code == 401
