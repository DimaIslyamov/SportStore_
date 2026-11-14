from uuid import uuid4

from tests.error_contract import error_content

import pytest
from django.urls import reverse
from rest_framework_simplejwt.tokens import AccessToken

from accounts.models import User
from catalog.models import Inventory
from orders.models import Order
from payments.models import Payment
from payments.services import create_payment

pytestmark = pytest.mark.django_db


def payment_url(order_id):
    return reverse("orders:payments", kwargs={"pk": order_id})


@pytest.mark.parametrize(
    "outcome, expected", [("success", "SUCCESS"), ("failed", "FAILED")]
)
def test_create_replay_and_conflict_stage13(
    api_client, customer, order_factory, outcome, expected
):
    order = order_factory(quantities=(2,))
    api_client.force_authenticate(user=customer)
    headers = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    payload = {"outcome": outcome}
    first = api_client.post(payment_url(order.pk), payload, format="json", **headers)
    assert first.status_code == 201
    body = first.json()
    assert body == {
        "id": Payment.objects.get().pk,
        "order": order.pk,
        "status": expected,
        "provider": "FAKE",
        "external_id": Payment.objects.get().external_id,
        "amount": "39.80",
        "currency": "PLN",
        "created_at": body["created_at"],
        "updated_at": body["updated_at"],
    }
    replay = api_client.post(payment_url(order.pk), payload, format="json", **headers)
    assert replay.status_code == 200 and replay.json() == body
    conflict = api_client.post(
        payment_url(order.pk),
        {"outcome": "failed" if outcome == "success" else "success"},
        format="json",
        **headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert Payment.objects.count() == 1


@pytest.mark.parametrize(
    "actor, expected",
    [
        ("anonymous", 401),
        ("inactive", 401),
        ("customer", 201),
        ("staff-customer", 201),
        ("super-customer", 201),
        ("manager", 403),
        ("super-manager", 403),
    ],
)
def test_jwt_roles_stage13(
    api_client, customer, manager, order_factory, actor, expected
):
    order = order_factory(quantities=(1,))
    if actor != "anonymous":
        user = manager if "manager" in actor else customer
        token = AccessToken.for_user(user)
        flags = {}
        if actor == "inactive":
            flags["is_active"] = False
        if actor.startswith("staff-") or actor.startswith("super-"):
            flags["is_staff"] = True
        if actor.startswith("super-"):
            flags["is_superuser"] = True
        User.objects.filter(pk=user.pk).update(**flags)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    response = api_client.post(
        payment_url(order.pk), {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
    )
    assert response.status_code == expected
    assert Payment.objects.count() == (1 if expected == 201 else 0)


def test_owner_filter_and_missing_order_stage13(
    api_client, customer, other_customer, order_factory
):
    foreign = order_factory(owner=other_customer, quantities=(1,))
    api_client.force_authenticate(user=customer)
    for order_id in (foreign.pk, 9_223_372_036_854_775_807):
        response = api_client.post(
            payment_url(order_id), {}, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
        )
        assert response.status_code == 404
        assert error_content(response) == {
            "code": "ORDER_NOT_FOUND",
            "message": "Order was not found.",
            "details": {},
        }
    assert not Payment.objects.exists()


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"outcome": "SUCCESS"}, "outcome"),
        ({"outcome": None}, "outcome"),
        ({"outcome": True}, "outcome"),
        ({"amount": "0.01"}, "amount"),
        ({"currency": "EUR"}, "currency"),
        ({"status": "SUCCESS"}, "status"),
        ({"provider": "FAKE"}, "provider"),
        ({"order": 1}, "order"),
        ({"key": str(uuid4())}, "key"),
        ({"external_id": "fake_x"}, "external_id"),
    ],
)
def test_strict_payload_stage13(api_client, customer, order_factory, payload, field):
    order = order_factory(quantities=(1,))
    api_client.force_authenticate(user=customer)
    response = api_client.post(
        payment_url(order.pk), payload, format="json", HTTP_IDEMPOTENCY_KEY=str(uuid4())
    )
    assert response.status_code == 400 and field in response.json()["error"]["details"]
    assert not Payment.objects.exists()


@pytest.mark.parametrize("key", [None, "", "not-a-uuid", "12345678"])
def test_key_required_stage13(api_client, customer, order_factory, key):
    order = order_factory(quantities=(1,))
    api_client.force_authenticate(user=customer)
    headers = {} if key is None else {"HTTP_IDEMPOTENCY_KEY": key}
    response = api_client.post(payment_url(order.pk), {}, format="json", **headers)
    assert (
        response.status_code == 400
        and "idempotency_key" in response.json()["error"]["details"]
    )
    assert not Payment.objects.exists()


def test_default_outcome_and_uuid_normalization_stage13(
    api_client, customer, order_factory
):
    order = order_factory(quantities=(1,))
    api_client.force_authenticate(user=customer)
    key = uuid4()
    first = api_client.post(
        payment_url(order.pk),
        {},
        format="json",
        HTTP_IDEMPOTENCY_KEY=f" {str(key).upper()} ",
    )
    second = api_client.post(
        payment_url(order.pk),
        {"outcome": "success"},
        format="json",
        HTTP_IDEMPOTENCY_KEY=str(key),
    )
    assert first.status_code == 201 and first.json()["status"] == "SUCCESS"
    assert second.status_code == 200 and second.json() == first.json()


def test_json_parser_and_methods_stage13(api_client, customer, order_factory):
    order = order_factory(quantities=(1,))
    api_client.force_authenticate(user=customer)
    url = payment_url(order.pk)
    headers = {"HTTP_IDEMPOTENCY_KEY": str(uuid4())}
    assert (
        api_client.post(
            url, "{", content_type="application/json", **headers
        ).status_code
        == 400
    )
    assert (
        api_client.post(
            url, "outcome=success", content_type="text/plain", **headers
        ).status_code
        == 415
    )
    assert api_client.post(url, [], format="json", **headers).status_code == 400
    for method in ("get", "head", "patch", "delete"):
        assert getattr(api_client, method)(url).status_code == 405
    assert not Payment.objects.exists()


def test_manager_cannot_mark_pending_paid_but_can_process_paid_stage13(
    api_client, customer, manager, order_factory, order_variants
):
    order = order_factory(quantities=(2,))
    status_url = reverse("orders:status", kwargs={"pk": order.pk})
    api_client.force_authenticate(user=manager)
    blocked = api_client.patch(status_url, {"status": "PAID"}, format="json")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "PAYMENT_CONFIRMATION_REQUIRED"
    create_payment(user=customer, order_id=order.pk, key=uuid4())
    accepted = api_client.patch(status_url, {"status": "PROCESSING"}, format="json")
    assert accepted.status_code == 200 and accepted.json()["status"] == "PROCESSING"
    api_client.force_authenticate(user=customer)
    cancelled = api_client.post(
        reverse("orders:cancel", kwargs={"pk": order.pk}), {}, format="json"
    )
    assert cancelled.status_code == 409
    assert cancelled.json()["error"]["code"] == "ORDER_CANNOT_BE_CANCELLED"
    assert Inventory.objects.get(variant=order_variants[0]).quantity == 8
    assert Payment.objects.get().status == "SUCCESS"
    assert Order.objects.get(pk=order.pk).status == "PROCESSING"
