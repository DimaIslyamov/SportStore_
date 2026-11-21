import base64
import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from accounts.models import Address
from cart.models import Cart
from orders.models import Order
from tests.error_contract import error_content


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
    assert access["exp"] - refresh["iat"] == 10 * 60
    assert refresh["exp"] - refresh["iat"] == 24 * 60 * 60


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


def test_deleted_user_cannot_use_existing_access(api_client, customer):
    access = str(AccessToken.for_user(customer))
    customer.delete()
    response = api_client.get(
        reverse("accounts:me"), HTTP_AUTHORIZATION=f"Bearer {access}"
    )

    assert response.status_code == 401
    assert error_content(response)["code"] == "user_not_found"
    assert response["WWW-Authenticate"].startswith("Bearer")


def test_tampered_access_token_is_rejected(api_client, customer, django_user_model):
    user_b = django_user_model.objects.create_user(
        email="customer-b@example.com",
        password="StrongPassword123!",
    )
    access = str(AccessToken.for_user(customer))
    models = (get_user_model(), Address, Order, Cart)
    before = [list(model.objects.order_by("pk").values()) for model in models]

    header, payload, signature = access.split(".")
    padding = "=" * (-len(payload) % 4)

    data = json.loads(base64.urlsafe_b64decode(payload + padding))
    data["user_id"] = user_b.pk

    new_payload = (
        base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    )

    forged_token = f"{header}.{new_payload}.{signature}"

    response = api_client.get(
        reverse("accounts:me"),
        HTTP_AUTHORIZATION=f"Bearer {forged_token}",
    )

    assert response.status_code == 401
    error = error_content(response)
    assert error["code"] == "token_not_valid"
    assert error["details"]["messages"][0]["token_type"] == "access"
    assert error["details"]["messages"][0]["token_class"] == "AccessToken"

    assert response["WWW-Authenticate"].startswith("Bearer")

    assert user_b.email not in response.content.decode()
    assert [list(model.objects.order_by("pk").values()) for model in models] == before
