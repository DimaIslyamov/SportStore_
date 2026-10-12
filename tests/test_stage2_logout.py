import pytest
from django.urls import reverse
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken


pytestmark = pytest.mark.django_db


def test_logout_blacklists_refresh_and_keeps_access_until_expiry(
    authenticated_client,
    api_client,
    tokens,
):
    response = authenticated_client.post(
        reverse("accounts:logout"),
        {"refresh": tokens["refresh"]},
        format="json",
    )

    assert response.status_code == 204
    assert response.content == b""

    refresh_response = api_client.post(
        reverse("accounts:refresh"),
        {"refresh": tokens["refresh"]},
        format="json",
    )
    assert refresh_response.status_code == 401

    profile_response = authenticated_client.get(reverse("accounts:me"))
    assert profile_response.status_code == 200


def test_logout_requires_access(api_client, tokens):
    response = api_client.post(
        reverse("accounts:logout"),
        {"refresh": tokens["refresh"]},
        format="json",
    )

    assert response.status_code == 401


def test_logout_rejects_refresh_of_another_user(authenticated_client, manager):
    refresh = RefreshToken.for_user(manager)

    response = authenticated_client.post(
        reverse("accounts:logout"),
        {"refresh": str(refresh)},
        format="json",
    )

    assert response.status_code == 403
    assert not BlacklistedToken.objects.filter(token__jti=refresh["jti"]).exists()


def test_logout_rejects_invalid_refresh(authenticated_client):
    response = authenticated_client.post(
        reverse("accounts:logout"),
        {"refresh": "not-a-jwt"},
        format="json",
    )

    assert response.status_code == 400
    assert "refresh" in response.json()


def test_logout_rejects_access_in_refresh_field(authenticated_client, tokens):
    response = authenticated_client.post(
        reverse("accounts:logout"),
        {"refresh": tokens["access"]},
        format="json",
    )

    assert response.status_code == 400


def test_logout_requires_refresh_field(authenticated_client):
    response = authenticated_client.post(reverse("accounts:logout"), {}, format="json")

    assert response.status_code == 400
    assert "refresh" in response.json()


def test_repeated_logout_returns_documented_error(authenticated_client, tokens):
    payload = {"refresh": tokens["refresh"]}

    first = authenticated_client.post(
        reverse("accounts:logout"), payload, format="json"
    )
    second = authenticated_client.post(
        reverse("accounts:logout"), payload, format="json"
    )

    assert first.status_code == 204
    assert second.status_code == 400
