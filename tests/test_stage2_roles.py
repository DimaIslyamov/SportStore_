import pytest
from django.contrib.auth import get_user_model
from django.urls import path, reverse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.permissions import IsCustomer, IsManager


class ManagerOnlyView(APIView):
    permission_classes = [IsAuthenticated, IsManager]

    def get(self, request):
        return Response({"detail": "manager endpoint"})


class CustomerOnlyView(APIView):
    permission_classes = [IsAuthenticated, IsCustomer]

    def get(self, request):
        return Response({"detail": "customer endpoint"})


urlpatterns = [
    path("manager/", ManagerOnlyView.as_view(), name="test-manager-only"),
    path("customer/", CustomerOnlyView.as_view(), name="test-customer-only"),
]

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def role_urlconf(settings):
    settings.ROOT_URLCONF = __name__


def authenticate(api_client, user):
    refresh = RefreshToken.for_user(user)
    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")


def test_manager_endpoint_rejects_anonymous_user(api_client):
    response = api_client.get(reverse("test-manager-only"))

    assert response.status_code == 401


def test_customer_cannot_use_manager_endpoint(api_client, customer):
    authenticate(api_client, customer)

    response = api_client.get(reverse("test-manager-only"))

    assert response.status_code == 403


def test_manager_can_use_manager_endpoint(api_client, manager):
    authenticate(api_client, manager)

    response = api_client.get(reverse("test-manager-only"))

    assert response.status_code == 200


def test_staff_customer_does_not_gain_manager_access(api_client, customer):
    customer.is_staff = True
    customer.save(update_fields=["is_staff", "updated_at"])
    authenticate(api_client, customer)

    response = api_client.get(reverse("test-manager-only"))

    assert response.status_code == 403


def test_superuser_can_use_manager_endpoint(api_client, raw_password):
    User = get_user_model()
    admin = User.objects.create_superuser(
        email="admin@example.com",
        password=raw_password,
        role=User.Role.CUSTOMER,
    )
    authenticate(api_client, admin)

    response = api_client.get(reverse("test-manager-only"))

    assert response.status_code == 200


def test_customer_can_use_customer_endpoint(api_client, customer):
    authenticate(api_client, customer)

    response = api_client.get(reverse("test-customer-only"))

    assert response.status_code == 200


def test_manager_cannot_use_customer_only_endpoint(api_client, manager):
    authenticate(api_client, manager)

    response = api_client.get(reverse("test-customer-only"))

    assert response.status_code == 403


def test_role_change_is_seen_by_existing_access(api_client, customer):
    authenticate(api_client, customer)
    first = api_client.get(reverse("test-manager-only"))
    assert first.status_code == 403

    User = get_user_model()
    customer.role = User.Role.MANAGER
    customer.save(update_fields=["role", "updated_at"])

    second = api_client.get(reverse("test-manager-only"))
    assert second.status_code == 200
