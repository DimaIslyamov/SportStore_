import json
from uuid import UUID, uuid4

import pytest
from django.db import OperationalError
from django.http import Http404, HttpResponse
from django.test import override_settings
from django.urls import path, reverse
from rest_framework.exceptions import Throttled
from rest_framework.permissions import AllowAny
from rest_framework.test import APIClient
from rest_framework.views import APIView

from core.logging import get_request_id


def broken(request):
    raise RuntimeError("SECRET-PASSWORD-TOKEN")


def missing(request):
    raise Http404("PRIVATE-DETAIL")


def denied_middleware_response(request):
    return HttpResponse("PRIVATE-HTML", status=403)


class Limited(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        raise Throttled(wait=7)


urlpatterns = [
    path("api/broken/", broken),
    path("api/missing/", missing),
    path("api/denied/", denied_middleware_response),
    path("api/limited/", Limited.as_view()),
]


def assert_error(response, status, code):
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    error = response.json()["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["code"] == code
    assert isinstance(error["message"], str)
    assert isinstance(error["details"], dict)
    assert error["request_id"] == response["X-Request-ID"]
    assert str(UUID(error["request_id"])) == error["request_id"]
    return error


@pytest.mark.django_db
def test_validation_auth_permissions_ownership_and_conflict_stage16(
    api_client, customer, manager, order_factory
):
    response = api_client.post(
        "/api/v1/auth/register/", {"error": "not-envelope"}, format="json"
    )
    error = assert_error(response, 400, "VALIDATION_ERROR")
    assert "error" in error["details"]
    assert_error(api_client.get("/api/v1/users/me/"), 401, "not_authenticated")
    api_client.force_authenticate(user=manager)
    assert_error(api_client.get("/api/v1/cart/"), 403, "permission_denied")
    api_client.force_authenticate(user=customer)
    assert_error(
        api_client.get("/api/v1/orders/9223372036854775807/"), 404, "ORDER_NOT_FOUND"
    )
    order = order_factory(quantities=(1,))
    api_client.force_authenticate(user=manager)
    response = api_client.patch(
        reverse("orders:status", kwargs={"pk": order.pk}),
        {"status": "PAID"},
        format="json",
    )
    assert_error(response, 409, "PAYMENT_CONFIRMATION_REQUIRED")


@pytest.mark.django_db
def test_parse_media_method_and_headers_stage16(api_client, customer):
    api_client.force_authenticate(user=customer)
    headers = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    url = reverse("orders:checkout")
    assert_error(
        api_client.post(url, "{", content_type="application/json", **headers),
        400,
        "parse_error",
    )
    assert_error(
        api_client.post(url, "x", content_type="text/plain", **headers),
        415,
        "unsupported_media_type",
    )
    response = api_client.get(url)
    assert_error(response, 405, "method_not_allowed")
    assert "POST" in response["Allow"]


@pytest.mark.parametrize("debug", [True, False])
def test_django_errors_and_safe_500_stage16(debug, settings, events):
    settings.ROOT_URLCONF = __name__
    settings.DEBUG = debug
    client = APIClient()
    for url, status, code in [
        ("/api/broken/", 500, "INTERNAL_ERROR"),
        ("/api/missing/", 404, "NOT_FOUND"),
        ("/api/unknown/", 404, "NOT_FOUND"),
        ("/api/denied/", 403, "PERMISSION_DENIED"),
    ]:
        response = client.get(url)
        assert_error(response, status, code)
        assert "PRIVATE" not in response.content.decode()
        assert "SECRET" not in response.content.decode()
    assert get_request_id() == "-"
    records = [
        r for r in events.records if r.name == "sportstore" and r.msg == "api_error"
    ]
    assert len(records) == 4
    assert (
        records[0].levelname == "ERROR" and records[0].exception_type == "RuntimeError"
    )
    assert all(r.levelname == "WARNING" for r in records[1:])
    assert "SECRET-PASSWORD-TOKEN" not in events.text


@override_settings(ROOT_URLCONF=__name__)
def test_throttle_preserves_retry_after_stage16():
    response = APIClient().get("/api/limited/")
    assert_error(response, 429, "throttled")
    assert response["Retry-After"] == "7"


def test_health_failure_and_success_stage16(api_client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OperationalError("PASSWORD DATABASE URL")

    monkeypatch.setattr("core.views.connection.cursor", unavailable)
    response = api_client.get("/api/v1/health/")
    error = assert_error(response, 503, "SERVICE_UNAVAILABLE")
    assert error["details"] == {"status": "error", "database": "unavailable"}
    assert "PASSWORD" not in json.dumps(response.json())


def test_authenticate_header_is_preserved_stage16(api_client):
    response = api_client.get("/api/v1/users/me/")
    assert_error(response, 401, "not_authenticated")
    assert response["WWW-Authenticate"].startswith("Bearer")
