import logging
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.urls import reverse
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from catalog.models import Inventory
from core.logging import JsonFormatter
from orders.models import IdempotencyRecord, Order
from payments.models import Payment
from payments.providers import FakePaymentProvider
from tests.error_contract import error_content
from tests.stage10.conftest import (
    checkout_catalog as checkout_catalog,
    checkout_factory as checkout_factory,
    checkout_state as checkout_state,
    other_checkout_customer as other_checkout_customer,
)
from tests.stage12.conftest import (
    order_factory as order_factory,
    order_variants as order_variants,
    prepare_order_cart as prepare_order_cart,
)
from tests.stage20.conftest import (
    check_response as check_response,
    openapi as openapi,
    operations as operations,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def throttle_clock(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(ScopedRateThrottle, "timer", staticmethod(lambda: now))

    def advance(seconds):
        nonlocal now
        now += seconds

    return advance


def rows(*models):
    return [list(model.objects.order_by("pk").values()) for model in models]


def assert_throttled(response, wait):
    assert response.status_code == 429
    assert response["Retry-After"] == str(wait)
    error = error_content(response)
    assert error["code"] == "throttled"
    assert error["message"]
    assert error["details"] == {}


def assert_safe_throttle_logs(response, records, *markers):
    logs = [
        JsonFormatter().format(record)
        for record in records
        if record.name == "sportstore"
        and getattr(record, "request_id", None) == response["X-Request-ID"]
    ]
    assert len(logs) == 1
    for marker in markers:
        assert marker not in logs[0]
        assert marker not in response.content.decode()


def test_auth_throttle_policy_uses_ip_and_separate_scopes(
    api_client,
    customer,
    raw_password,
    registration_payload,
    throttle_clock,
    caplog,
    check_response,
):
    login = reverse("accounts:token")
    register = reverse("accounts:register")
    wrong_password = "stage22-wrong-password-marker"
    credentials = {"email": customer.email, "password": wrong_password}
    for _ in range(5):
        assert api_client.post(login, credentials, format="json").status_code == 401

    before = rows(User, OutstandingToken, BlacklistedToken)
    api_client.cookies["sessionid"] = "stage22-cookie-marker"
    with caplog.at_level(logging.WARNING, logger="sportstore"):
        blocked = api_client.post(
            f"{login}?tracking=stage22-query-marker",
            {"email": customer.email, "password": raw_password},
            format="json",
            HTTP_X_FORWARDED_FOR="192.0.2.20",
            HTTP_AUTHORIZATION="Bearer stage22-header-marker",
        )
    assert_throttled(blocked, 60)
    check_response("auth_token_create", blocked, 429)
    assert rows(User, OutstandingToken, BlacklistedToken) == before
    assert_safe_throttle_logs(
        blocked,
        caplog.records,
        customer.email,
        raw_password,
        "stage22-header-marker",
        "stage22-cookie-marker",
        "stage22-query-marker",
    )

    assert (
        api_client.post(
            login, credentials, format="json", REMOTE_ADDR="192.0.2.21"
        ).status_code
        == 401
    )

    for index in range(5):
        registration_payload["email"] = f"register-{index}@example.com"
        response = api_client.post(register, registration_payload, format="json")
        assert response.status_code == 201
        assert response.json()["role"] == User.Role.CUSTOMER

    registration_payload["email"] = "registration-blocked@example.com"
    before = rows(User, OutstandingToken, BlacklistedToken)
    blocked_registration = api_client.post(
        register, registration_payload, format="json"
    )
    assert_throttled(blocked_registration, 3600)
    assert blocked_registration["X-Request-ID"] != blocked["X-Request-ID"]
    assert rows(User, OutstandingToken, BlacklistedToken) == before

    throttle_clock(60)
    assert (
        api_client.post(
            login, {"email": customer.email, "password": raw_password}, format="json"
        ).status_code
        == 200
    )
    assert_throttled(
        api_client.post(register, registration_payload, format="json"), 3540
    )
    throttle_clock(3540)
    assert (
        api_client.post(register, registration_payload, format="json").status_code
        == 201
    )


def test_refresh_throttle_does_not_rotate_token_until_window_expires(
    api_client, tokens, registration_payload, throttle_clock, caplog
):
    url = reverse("accounts:refresh")
    refresh = tokens["refresh"]
    for _ in range(30):
        response = api_client.post(url, {"refresh": refresh}, format="json")
        assert response.status_code == 200
        previous, refresh = refresh, response.json()["refresh"]
        assert previous != refresh

    before = rows(OutstandingToken, BlacklistedToken)
    with caplog.at_level(logging.WARNING, logger="sportstore"):
        blocked = api_client.post(url, {"refresh": refresh}, format="json")
    assert_throttled(blocked, 60)
    assert_safe_throttle_logs(blocked, caplog.records, refresh)
    assert rows(OutstandingToken, BlacklistedToken) == before
    assert BlacklistedToken.objects.count() == 30
    assert (
        api_client.post(
            reverse("accounts:register"), registration_payload, format="json"
        ).status_code
        == 201
    )

    throttle_clock(60)
    response = api_client.post(url, {"refresh": refresh}, format="json")
    assert response.status_code == 200
    assert response.json()["refresh"] != refresh
    assert BlacklistedToken.objects.count() == 31


def customer_client(user):
    return APIClient(HTTP_AUTHORIZATION=f"Bearer {AccessToken.for_user(user)}")


@pytest.fixture
def one_request_per_business_scope(monkeypatch, settings):
    assert settings.REST_FRAMEWORK["NUM_PROXIES"] == 0
    assert ScopedRateThrottle.THROTTLE_RATES == {
        "auth_login": "5/min",
        "auth_register": "5/hour",
        "auth_refresh": "30/min",
        "checkout": "10/min",
        "payment": "10/min",
    }
    monkeypatch.setattr(
        ScopedRateThrottle,
        "THROTTLE_RATES",
        {**ScopedRateThrottle.THROTTLE_RATES, "checkout": "1/min", "payment": "1/min"},
    )


@pytest.mark.django_db(transaction=True)
def test_checkout_throttle_does_not_write_and_allows_replay(
    customer,
    other_checkout_customer,
    checkout_factory,
    checkout_state,
    throttle_clock,
    one_request_per_business_scope,
):
    url = reverse("orders:checkout")
    first = checkout_factory(quantities=(1,))
    client = customer_client(customer)
    key = str(uuid4())
    payload = {"address_id": first.address.pk}
    accepted = client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert accepted.status_code == 201

    second = checkout_factory(quantities=(1,))
    new_client = customer_client(customer)
    second_key = str(uuid4())
    second_payload = {"address_id": second.address.pk}
    before = checkout_state(), rows(IdempotencyRecord)
    blocked = new_client.post(
        url,
        second_payload,
        format="json",
        HTTP_IDEMPOTENCY_KEY=second_key,
    )
    assert_throttled(blocked, 60)
    assert_throttled(
        new_client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY=key), 60
    )
    assert (checkout_state(), rows(IdempotencyRecord)) == before

    other = checkout_factory(owner=other_checkout_customer, quantities=(1,))
    assert (
        customer_client(other.user)
        .post(
            url,
            {"address_id": other.address.pk},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        .status_code
        == 201
    )

    before = checkout_state(), rows(IdempotencyRecord)
    throttle_clock(60)
    replay = client.post(url, payload, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert replay.status_code == 201
    assert replay.json() == accepted.json()
    assert replay["X-Request-ID"] != accepted["X-Request-ID"]
    assert (checkout_state(), rows(IdempotencyRecord)) == before

    throttle_clock(60)
    retried = new_client.post(
        url, second_payload, format="json", HTTP_IDEMPOTENCY_KEY=second_key
    )
    assert retried.status_code == 201
    assert retried.json()["id"] != accepted.json()["id"]
    assert Order.objects.filter(user=customer).count() == 2


@pytest.mark.django_db(transaction=True)
def test_payment_throttle_is_shared_across_orders_and_preserves_replay(
    customer,
    other_checkout_customer,
    order_factory,
    prepare_order_cart,
    throttle_clock,
    one_request_per_business_scope,
):
    first = order_factory(quantities=(1,))
    second = order_factory(quantities=(1,))
    other = order_factory(owner=other_checkout_customer, quantities=(1,))
    client = customer_client(customer)
    address = prepare_order_cart(owner=customer, quantities=(1,))
    checkout = client.post(
        reverse("orders:checkout"),
        {"address_id": address.pk},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(uuid4()),
    )
    assert checkout.status_code == 201

    url = reverse("orders:payments", args=[first.pk])
    key = str(uuid4())
    accepted = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY=key)
    assert accepted.status_code == 201
    second_url = reverse("orders:payments", args=[second.pk])
    second_key = str(uuid4())
    before = rows(Payment, Order, Inventory)
    with patch.object(FakePaymentProvider, "pay", autospec=True) as provider:
        blocked = customer_client(customer).post(
            second_url,
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=second_key,
        )
        assert_throttled(blocked, 60)
        assert_throttled(
            client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY=key), 60
        )
        provider.assert_not_called()
    assert rows(Payment, Order, Inventory) == before

    assert (
        customer_client(other_checkout_customer)
        .post(
            reverse("orders:payments", args=[other.pk]),
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        .status_code
        == 201
    )

    before = rows(Payment, Order, Inventory)
    throttle_clock(60)
    with patch.object(FakePaymentProvider, "pay", autospec=True) as provider:
        replay = client.post(url, {}, format="json", HTTP_IDEMPOTENCY_KEY=key)
        assert replay.status_code == 200
        provider.assert_not_called()
    assert replay.json() == accepted.json()
    assert replay["X-Request-ID"] != accepted["X-Request-ID"]
    assert rows(Payment, Order, Inventory) == before

    throttle_clock(60)
    retried = client.post(
        second_url, {}, format="json", HTTP_IDEMPOTENCY_KEY=second_key
    )
    assert retried.status_code == 201
    assert Payment.objects.get(order=second).status == Payment.Status.SUCCESS
