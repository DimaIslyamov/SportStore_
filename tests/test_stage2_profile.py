from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken


pytestmark = pytest.mark.django_db


def test_profile_requires_authentication(api_client):
    response = api_client.get(reverse("accounts:me"))

    assert response.status_code == 401


def test_profile_update_requires_authentication(api_client):
    response = api_client.patch(
        reverse("accounts:me"), {"first_name": "Changed"}, format="json"
    )

    assert response.status_code == 401


def test_profile_returns_only_current_user(authenticated_client, customer, manager):
    response = authenticated_client.get(reverse("accounts:me"))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == customer.pk
    assert body["id"] != manager.pk
    assert body["email"] == customer.email
    assert "password" not in body
    assert "is_staff" not in body
    assert "is_superuser" not in body


def test_manager_can_read_own_profile(api_client, manager):
    refresh = RefreshToken.for_user(manager)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")

    response = api_client.get(reverse("accounts:me"))

    assert response.status_code == 200
    assert response.json()["id"] == manager.pk
    assert response.json()["role"] == "MANAGER"


def test_profile_updates_allowed_fields(authenticated_client, customer, manager):
    response = authenticated_client.patch(
        reverse("accounts:me"),
        {"first_name": "Dima", "last_name": "Student", "phone": "+48123456789"},
        format="json",
    )

    assert response.status_code == 200
    customer.refresh_from_db()
    manager.refresh_from_db()
    assert customer.first_name == "Dima"
    assert customer.last_name == "Student"
    assert customer.phone == "+48123456789"
    assert manager.first_name == ""


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 999),
        ("email", "another@example.com"),
        ("role", "MANAGER"),
        ("is_staff", True),
        ("is_superuser", True),
        ("is_active", False),
        ("password", "another-password"),
        ("created_at", "2020-01-01T00:00:00Z"),
        ("updated_at", "2020-01-01T00:00:00Z"),
        ("unexpected", "value"),
    ],
)
def test_profile_rejects_protected_fields(
    authenticated_client,
    customer,
    field,
    value,
):
    original_email = customer.email
    original_password = customer.password

    response = authenticated_client.patch(
        reverse("accounts:me"), {field: value}, format="json"
    )

    assert response.status_code == 400
    assert field in response.json()
    customer.refresh_from_db()
    User = get_user_model()
    assert customer.email == original_email
    assert customer.password == original_password
    assert customer.role == User.Role.CUSTOMER
    assert customer.is_staff is False
    assert customer.is_superuser is False
    assert customer.is_active is True


def test_profile_moves_updated_at_and_preserves_created_at(
    authenticated_client,
    customer,
):
    created_at = customer.created_at
    future = customer.updated_at + timedelta(minutes=1)

    with patch("django.db.models.fields.timezone.now", return_value=future):
        response = authenticated_client.patch(
            reverse("accounts:me"), {"first_name": "Changed"}, format="json"
        )

    assert response.status_code == 200
    customer.refresh_from_db()
    assert customer.created_at == created_at
    assert customer.updated_at == future


@pytest.mark.parametrize("method", ["put", "delete", "post"])
def test_profile_rejects_unsupported_methods(authenticated_client, method):
    response = getattr(authenticated_client, method)(
        reverse("accounts:me"), {}, format="json"
    )

    assert response.status_code == 405


def test_inactive_user_cannot_use_existing_access(
    authenticated_client,
    customer,
):
    customer.is_active = False
    customer.save(update_fields=["is_active", "updated_at"])

    response = authenticated_client.get(reverse("accounts:me"))

    assert response.status_code == 401
