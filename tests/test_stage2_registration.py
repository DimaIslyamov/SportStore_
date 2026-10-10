import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.exceptions import ValidationError

from accounts.serializers import RegisterSerializer


pytestmark = pytest.mark.django_db


def test_registration_creates_customer_and_hides_secrets(
    api_client,
    registration_payload,
    raw_password,
):
    response = api_client.post(
        reverse("accounts:register"),
        registration_payload,
        format="json",
    )

    assert response.status_code == 201
    body = response.json()
    User = get_user_model()
    user = User.objects.get(pk=body["id"])

    assert user.email == registration_payload["email"]
    assert user.role == User.Role.CUSTOMER
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.check_password(raw_password)
    assert "password" not in body
    assert "password_confirm" not in body
    assert "is_staff" not in body
    assert "is_superuser" not in body
    assert "access" not in body


def test_registration_normalizes_email(api_client, registration_payload):
    registration_payload["email"] = " New.Customer@Example.COM "

    response = api_client.post(
        reverse("accounts:register"), registration_payload, format="json"
    )

    assert response.status_code == 201
    assert response.json()["email"] == "new.customer@example.com"


def test_registration_rejects_duplicate_email(
    api_client,
    registration_payload,
    customer,
):
    registration_payload["email"] = customer.email.upper()

    response = api_client.post(
        reverse("accounts:register"), registration_payload, format="json"
    )

    assert response.status_code == 400
    assert "email" in response.json()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("role", "MANAGER"),
        ("is_staff", True),
        ("is_superuser", True),
        ("is_active", False),
        ("id", 999),
        ("unexpected", "value"),
    ],
)
def test_registration_rejects_forbidden_input(
    api_client,
    registration_payload,
    field,
    value,
):
    registration_payload[field] = value
    User = get_user_model()

    response = api_client.post(
        reverse("accounts:register"), registration_payload, format="json"
    )

    assert response.status_code == 400
    assert field in response.json()
    assert not User.objects.filter(email=registration_payload["email"]).exists()


def test_registration_rejects_password_mismatch(api_client, registration_payload):
    registration_payload["password_confirm"] = "another-password"

    response = api_client.post(
        reverse("accounts:register"), registration_payload, format="json"
    )

    assert response.status_code == 400
    assert "password_confirm" in response.json()


@pytest.mark.parametrize("password", ["abc", "123456789", "password"])
def test_registration_rejects_weak_password(
    api_client,
    registration_payload,
    password,
):
    registration_payload["password"] = password
    registration_payload["password_confirm"] = password

    response = api_client.post(
        reverse("accounts:register"), registration_payload, format="json"
    )

    assert response.status_code == 400
    assert "password" in response.json()


def test_registration_rejects_invalid_email(api_client, registration_payload):
    registration_payload["email"] = "not-an-email"

    response = api_client.post(
        reverse("accounts:register"), registration_payload, format="json"
    )

    assert response.status_code == 400
    assert "email" in response.json()


def test_two_validated_registrations_get_controlled_duplicate_error(
    registration_payload,
):
    first = RegisterSerializer(data=registration_payload.copy())
    second = RegisterSerializer(data=registration_payload.copy())
    assert first.is_valid(), first.errors
    assert second.is_valid(), second.errors

    first.save()

    with pytest.raises(ValidationError) as exc_info:
        second.save()

    assert "email" in exc_info.value.detail
    User = get_user_model()
    assert User.objects.filter(email=registration_payload["email"]).count() == 1
