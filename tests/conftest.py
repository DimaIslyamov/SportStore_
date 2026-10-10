import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


@pytest.fixture
def raw_password():
    return "Zp9!R6m4#T2q8A"


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def customer(db, raw_password):
    User = get_user_model()
    return User.objects.create_user(
        email="customer@example.com",
        password=raw_password,
        first_name="Customer",
    )


@pytest.fixture
def manager(db, raw_password):
    User = get_user_model()
    return User.objects.create_user(
        email="manager@example.com",
        password=raw_password,
        role=User.Role.MANAGER,
    )


@pytest.fixture
def tokens(customer):
    refresh = RefreshToken.for_user(customer)
    return {
        "access": str(refresh.access_token),
        "refresh": str(refresh),
    }


@pytest.fixture
def authenticated_client(api_client, tokens):
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    return api_client


@pytest.fixture
def registration_payload(raw_password):
    return {
        "email": "new.customer@example.com",
        "password": raw_password,
        "password_confirm": raw_password,
        "first_name": "New",
        "last_name": "Customer",
        "phone": "+48123456789",
    }
