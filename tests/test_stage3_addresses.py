import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import Address
from accounts.serializers import AddressSerializer


pytestmark = pytest.mark.django_db


@pytest.fixture
def address_payload():
    return {
        "first_name": "Anna",
        "last_name": "Kowalska",
        "phone": "+48123456789",
        "country": "Poland",
        "city": "Warsaw",
        "postal_code": "00-001",
        "street": "Marszalkowska",
        "building": "12A",
        "apartment": "3B",
    }


@pytest.fixture
def address_factory(db, address_payload):
    def create_address(user, **changes):
        payload = {**address_payload, **changes}
        return Address.objects.create(user=user, **payload)

    return create_address


@pytest.fixture
def other_customer(db, raw_password):
    User = get_user_model()
    return User.objects.create_user(
        email="other.customer@example.com",
        password=raw_password,
        role=User.Role.CUSTOMER,
    )


@pytest.fixture
def other_customer_client(other_customer):
    client = APIClient()
    access = str(RefreshToken.for_user(other_customer).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def manager_client(manager):
    client = APIClient()
    access = str(RefreshToken.for_user(manager).access_token)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    return client


@pytest.fixture
def own_address(address_factory, customer):
    return address_factory(customer, is_default=True)


@pytest.fixture
def foreign_address(address_factory, other_customer):
    return address_factory(other_customer, is_default=True)


def address_state(address):
    return Address.objects.values().get(pk=address.pk)


def send_request(client, method, url, payload):
    if method in {"post", "patch", "put"}:
        return getattr(client, method)(url, payload, format="json")
    return getattr(client, method)(url)


@pytest.mark.parametrize(
    ("actor", "expected_status"),
    [("anonymous", 401), ("manager", 403)],
)
@pytest.mark.parametrize(
    ("method", "target"),
    [
        ("get", "list"),
        ("post", "list"),
        ("get", "detail"),
        ("patch", "detail"),
        ("delete", "detail"),
    ],
)
def test_address_api_requires_customer(
    api_client,
    manager_client,
    own_address,
    address_payload,
    actor,
    expected_status,
    method,
    target,
):
    client = api_client if actor == "anonymous" else manager_client
    if target == "list":
        url = reverse("accounts:address-list")
    else:
        url = reverse("accounts:address-detail", args=[own_address.pk])
    before = address_state(own_address)
    count_before = Address.objects.count()

    response = send_request(client, method, url, address_payload)

    assert response.status_code == expected_status
    assert Address.objects.count() == count_before
    assert address_state(own_address) == before


@pytest.mark.parametrize("default_choice", [None, True])
def test_customer_creates_address_with_server_owner(
    authenticated_client, customer, address_payload, default_choice
):
    payload = dict(address_payload)
    payload.pop("apartment")
    if default_choice is not None:
        payload["is_default"] = default_choice

    response = authenticated_client.post(
        reverse("accounts:address-list"), payload, format="json"
    )

    assert response.status_code == 201
    body = response.json()
    address = Address.objects.get(pk=body["id"])
    assert address.user_id == customer.pk
    assert body["user"] == customer.pk
    assert address.apartment == body["apartment"] == ""
    expected_default = default_choice is True
    assert address.is_default is expected_default
    assert body["is_default"] is expected_default
    assert body["created_at"]
    assert body["updated_at"]
    assert Address.objects.count() == 1


def test_list_contains_only_own_addresses(
    authenticated_client, customer, own_address, foreign_address, address_factory
):
    second_own = address_factory(customer)

    response = authenticated_client.get(reverse("accounts:address-list"))

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert [item["id"] for item in body] == [second_own.pk, own_address.pk]
    assert all(item["user"] == customer.pk for item in body)
    assert foreign_address.pk not in {item["id"] for item in body}


def test_list_is_empty_when_customer_has_no_addresses(
    authenticated_client, foreign_address
):
    response = authenticated_client.get(reverse("accounts:address-list"))

    assert response.status_code == 200
    assert response.json() == []
    assert Address.objects.filter(pk=foreign_address.pk).exists()


def test_customer_reads_own_address(authenticated_client, customer, own_address):
    response = authenticated_client.get(
        reverse("accounts:address-detail", args=[own_address.pk])
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == own_address.pk
    assert body["user"] == customer.pk
    assert body["city"] == own_address.city
    assert body["is_default"] is True


def test_customer_partially_updates_own_address(
    authenticated_client, customer, own_address
):
    original_name = own_address.first_name
    original_created_at = own_address.created_at

    response = authenticated_client.patch(
        reverse("accounts:address-detail", args=[own_address.pk]),
        {"city": "Krakow", "building": "20B"},
        format="json",
    )

    assert response.status_code == 200
    own_address.refresh_from_db()
    assert own_address.city == response.json()["city"] == "Krakow"
    assert own_address.building == "20B"
    assert own_address.first_name == original_name
    assert own_address.user_id == customer.pk
    assert own_address.created_at == original_created_at
    assert own_address.is_default is True


def test_customer_deletes_own_address(authenticated_client, own_address):
    response = authenticated_client.delete(
        reverse("accounts:address-detail", args=[own_address.pk])
    )

    assert response.status_code == 204
    assert response.content == b""
    assert not Address.objects.filter(pk=own_address.pk).exists()


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_foreign_address_is_hidden_and_unchanged(
    authenticated_client, own_address, foreign_address, method
):
    own_before = address_state(own_address)
    foreign_before = address_state(foreign_address)
    count_before = Address.objects.count()

    response = send_request(
        authenticated_client,
        method,
        reverse("accounts:address-detail", args=[foreign_address.pk]),
        {"city": "Changed by stranger", "is_default": True},
    )

    assert response.status_code == 404
    assert Address.objects.count() == count_before
    assert address_state(foreign_address) == foreign_before
    assert address_state(own_address) == own_before


def test_second_customer_cannot_read_first_customers_address(
    other_customer_client, own_address
):
    response = other_customer_client.get(
        reverse("accounts:address-detail", args=[own_address.pk])
    )

    assert response.status_code == 404
    assert Address.objects.filter(pk=own_address.pk).exists()


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("field", ["user", "user_id"])
def test_client_cannot_submit_owner(
    authenticated_client,
    own_address,
    other_customer,
    address_payload,
    method,
    field,
):
    payload = dict(address_payload) if method == "post" else {"city": "Changed"}
    payload[field] = other_customer.pk
    payload["is_default"] = True
    url = (
        reverse("accounts:address-list")
        if method == "post"
        else reverse("accounts:address-detail", args=[own_address.pk])
    )
    before = address_state(own_address)
    count_before = Address.objects.count()

    response = send_request(authenticated_client, method, url, payload)

    assert response.status_code == 400
    assert field in response.json()
    assert Address.objects.count() == count_before
    assert address_state(own_address) == before
    assert not Address.objects.filter(user=other_customer).exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 99999),
        ("created_at", "2020-01-01T00:00:00Z"),
        ("updated_at", "2020-01-01T00:00:00Z"),
        ("unexpected", "value"),
    ],
)
def test_patch_rejects_read_only_and_unknown_fields(
    authenticated_client, own_address, field, value
):
    before = address_state(own_address)

    response = authenticated_client.patch(
        reverse("accounts:address-detail", args=[own_address.pk]),
        {field: value},
        format="json",
    )

    assert response.status_code == 400
    assert field in response.json()
    assert address_state(own_address) == before


@pytest.mark.parametrize(
    "missing_field",
    [
        "first_name",
        "last_name",
        "phone",
        "country",
        "city",
        "postal_code",
        "street",
        "building",
    ],
)
def test_create_requires_delivery_fields(
    authenticated_client, address_payload, missing_field
):
    payload = dict(address_payload)
    payload.pop(missing_field)

    response = authenticated_client.post(
        reverse("accounts:address-list"), payload, format="json"
    )

    assert response.status_code == 400
    assert missing_field in response.json()
    assert not Address.objects.exists()


@pytest.mark.parametrize(
    ("method", "field", "value"),
    [
        ("post", "city", "   "),
        ("post", "street", None),
        ("post", "postal_code", "x" * 21),
        ("patch", "city", ""),
        ("patch", "is_default", "not-a-boolean"),
    ],
)
def test_invalid_input_does_not_change_database(
    authenticated_client, own_address, address_payload, method, field, value
):
    payload = dict(address_payload) if method == "post" else {}
    payload[field] = value
    url = (
        reverse("accounts:address-list")
        if method == "post"
        else reverse("accounts:address-detail", args=[own_address.pk])
    )
    before = address_state(own_address)
    count_before = Address.objects.count()

    response = send_request(authenticated_client, method, url, payload)

    assert response.status_code == 400
    assert field in response.json()
    assert Address.objects.count() == count_before
    assert address_state(own_address) == before


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_missing_address_returns_404(authenticated_client, own_address, method):
    missing_id = own_address.pk + 1
    assert not Address.objects.filter(pk=missing_id).exists()
    before = address_state(own_address)

    response = send_request(
        authenticated_client,
        method,
        reverse("accounts:address-detail", args=[missing_id]),
        {"city": "Changed"},
    )

    assert response.status_code == 404
    assert Address.objects.count() == 1
    assert address_state(own_address) == before


def test_put_is_not_supported(authenticated_client, own_address, address_payload):
    before = address_state(own_address)

    response = authenticated_client.put(
        reverse("accounts:address-detail", args=[own_address.pk]),
        address_payload,
        format="json",
    )

    assert response.status_code == 405
    assert address_state(own_address) == before


def test_creating_ordinary_address_preserves_default(
    authenticated_client, own_address, address_payload
):
    response = authenticated_client.post(
        reverse("accounts:address-list"), address_payload, format="json"
    )

    assert response.status_code == 201
    own_address.refresh_from_db()
    assert own_address.is_default is True
    assert response.json()["is_default"] is False
    defaults = Address.objects.filter(user_id=own_address.user_id, is_default=True)
    assert defaults.count() == 1


def test_creating_default_replaces_only_own_default(
    authenticated_client, customer, own_address, foreign_address, address_payload
):
    foreign_before = address_state(foreign_address)

    response = authenticated_client.post(
        reverse("accounts:address-list"),
        {**address_payload, "is_default": True},
        format="json",
    )

    assert response.status_code == 201
    own_address.refresh_from_db()
    assert own_address.is_default is False
    selected = Address.objects.get(user=customer, is_default=True)
    assert selected.pk == response.json()["id"]
    assert response.json()["is_default"] is True
    assert address_state(foreign_address) == foreign_before


def test_patch_can_move_default_and_repeat_same_selection(
    authenticated_client, customer, own_address, foreign_address, address_factory
):
    second = address_factory(customer)
    foreign_before = address_state(foreign_address)
    url = reverse("accounts:address-detail", args=[second.pk])

    first_response = authenticated_client.patch(
        url, {"is_default": True}, format="json"
    )
    repeat_response = authenticated_client.patch(
        url, {"is_default": True}, format="json"
    )

    assert first_response.status_code == repeat_response.status_code == 200
    own_address.refresh_from_db()
    second.refresh_from_db()
    assert own_address.is_default is False
    assert second.is_default is True
    assert Address.objects.get(user=customer, is_default=True).pk == second.pk
    assert address_state(foreign_address) == foreign_before


def test_customer_can_clear_default(authenticated_client, customer, own_address):
    response = authenticated_client.patch(
        reverse("accounts:address-detail", args=[own_address.pk]),
        {"is_default": False},
        format="json",
    )

    assert response.status_code == 200
    own_address.refresh_from_db()
    assert own_address.is_default is False
    assert response.json()["is_default"] is False
    assert not Address.objects.filter(user=customer, is_default=True).exists()


def test_deleting_default_does_not_select_replacement(
    authenticated_client, customer, own_address, foreign_address, address_factory
):
    ordinary = address_factory(customer)
    foreign_before = address_state(foreign_address)

    response = authenticated_client.delete(
        reverse("accounts:address-detail", args=[own_address.pk])
    )

    assert response.status_code == 204
    assert not Address.objects.filter(pk=own_address.pk).exists()
    ordinary.refresh_from_db()
    assert ordinary.is_default is False
    assert not Address.objects.filter(user=customer, is_default=True).exists()
    assert address_state(foreign_address) == foreign_before


def test_deleting_ordinary_address_preserves_default(
    authenticated_client, customer, own_address, address_factory
):
    ordinary = address_factory(customer)

    response = authenticated_client.delete(
        reverse("accounts:address-detail", args=[ordinary.pk])
    )

    assert response.status_code == 204
    assert not Address.objects.filter(pk=ordinary.pk).exists()
    own_address.refresh_from_db()
    assert own_address.is_default is True


def test_invalid_default_switch_keeps_previous_selection(
    authenticated_client, customer, own_address, address_factory
):
    second = address_factory(customer)
    first_before = address_state(own_address)
    second_before = address_state(second)

    response = authenticated_client.patch(
        reverse("accounts:address-detail", args=[second.pk]),
        {"city": "", "is_default": True},
        format="json",
    )

    assert response.status_code == 400
    assert "city" in response.json()
    assert address_state(own_address) == first_before
    assert address_state(second) == second_before


def test_failed_save_rolls_back_previous_default(
    authenticated_client, own_address, address_payload, monkeypatch
):
    before = address_state(own_address)
    count_before = Address.objects.count()

    def fail_create(self, validated_data):
        raise IntegrityError("Simulated failure after clearing the old default")

    monkeypatch.setattr(AddressSerializer, "create", fail_create)

    with pytest.raises(IntegrityError):
        authenticated_client.post(
            reverse("accounts:address-list"),
            {**address_payload, "is_default": True},
            format="json",
        )

    assert Address.objects.count() == count_before
    assert address_state(own_address) == before


def test_database_rejects_two_defaults_for_same_user(
    customer, other_customer, own_address, address_factory
):
    address_factory(customer)
    other_default = address_factory(other_customer, is_default=True)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            address_factory(customer, is_default=True)

    assert Address.objects.filter(user=customer).count() == 2
    assert Address.objects.get(user=customer, is_default=True).pk == own_address.pk
    selected_other = Address.objects.get(user=other_customer, is_default=True)
    assert selected_other.pk == other_default.pk