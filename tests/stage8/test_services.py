import pytest
from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError

from cart import services
from cart.models import Cart, CartItem
from catalog.models import Inventory

pytestmark = pytest.mark.django_db


def test_cart_creation_is_idempotent(customer):
    first = services.get_or_create_cart(user=customer)
    second = services.get_or_create_cart(user=customer)
    assert first.pk == second.pk
    assert first.updated_at == second.updated_at
    assert Cart.objects.filter(user=customer).count() == 1


def test_manager_cannot_use_cart_services(manager):
    with pytest.raises(PermissionDenied):
        services.get_or_create_cart(user=manager)
    assert Cart.objects.count() == 0


def test_add_increments_update_sets_remove_deletes(customer, cart_variant_factory):
    variant = cart_variant_factory(stock=20)
    item, created = services.add_item(user=customer, variant_id=variant.pk, quantity=2)
    assert created is True and item.quantity == 2
    same, created = services.add_item(user=customer, variant_id=variant.pk, quantity=3)
    assert created is False and same.pk == item.pk and same.quantity == 5
    updated = services.update_item_quantity(user=customer, item_id=item.pk, quantity=1)
    assert updated.quantity == 1
    assert CartItem.objects.count() == 1
    services.remove_item(user=customer, item_id=item.pk)
    assert not CartItem.objects.exists()
    assert Cart.objects.filter(user=customer).count() == 1


def test_clear_keeps_cart_and_other_customers_items(
    customer, other_customer, cart_variant_factory
):
    first, second = cart_variant_factory(), cart_variant_factory()
    for variant in [first, second]:
        services.add_item(user=customer, variant_id=variant.pk, quantity=1)
    foreign, _ = services.add_item(user=other_customer, variant_id=first.pk, quantity=4)
    cart = services.get_or_create_cart(user=customer)
    services.clear_cart(user=customer)
    assert not CartItem.objects.filter(cart=cart).exists()
    assert Cart.objects.filter(pk=cart.pk).exists()
    assert CartItem.objects.get(pk=foreign.pk).quantity == 4
    cart.refresh_from_db()
    stamp = cart.updated_at
    services.clear_cart(user=customer)
    cart.refresh_from_db()
    assert cart.updated_at == stamp


def test_clear_missing_cart_is_noop_without_creation(customer):
    assert not Cart.objects.filter(user=customer).exists()
    assert services.clear_cart(user=customer) is None
    assert services.clear_cart(user=customer) is None
    assert not Cart.objects.filter(user=customer).exists()
    assert not CartItem.objects.exists()


@pytest.mark.parametrize("method", ["update", "remove"])
def test_missing_item_service_never_attempts_cart_creation(
    customer, monkeypatch, method
):
    def forbid_creation(**kwargs):
        raise AssertionError("Missing item must not create a Cart")

    monkeypatch.setattr(services, "get_or_create_cart", forbid_creation)
    with pytest.raises(Http404):
        if method == "update":
            services.update_item_quantity(user=customer, item_id=999999, quantity=2)
        else:
            services.remove_item(user=customer, item_id=999999)
    assert not Cart.objects.filter(user=customer).exists()
    assert not CartItem.objects.exists()


@pytest.mark.parametrize("quantity", [0, -1, 1000, True, 1.5, "2", None])
def test_direct_service_rejects_invalid_quantity(
    customer, cart_variant_factory, quantity
):
    variant = cart_variant_factory()
    with pytest.raises(ValidationError):
        services.add_item(user=customer, variant_id=variant.pk, quantity=quantity)
    assert Cart.objects.count() == 0


def test_limit_applies_to_increment_total(customer, cart_variant_factory):
    variant = cart_variant_factory()
    item, _ = services.add_item(user=customer, variant_id=variant.pk, quantity=999)
    with pytest.raises(ValidationError):
        services.add_item(user=customer, variant_id=variant.pk, quantity=1)
    item.refresh_from_db()
    assert item.quantity == 999


@pytest.mark.parametrize("method", ["update", "remove"])
def test_services_hide_foreign_item(
    customer, other_customer, cart_variant_factory, method
):
    variant = cart_variant_factory()
    foreign, _ = services.add_item(
        user=other_customer, variant_id=variant.pk, quantity=3
    )
    with pytest.raises(Http404):
        if method == "update":
            services.update_item_quantity(user=customer, item_id=foreign.pk, quantity=2)
        else:
            services.remove_item(user=customer, item_id=foreign.pk)
    foreign.refresh_from_db()
    assert foreign.quantity == 3
    assert not Cart.objects.filter(user=customer).exists()


@pytest.mark.parametrize("stock", [None, 0, 1])
def test_cart_never_changes_inventory(customer, cart_variant_factory, stock):
    variant = cart_variant_factory(stock=stock)
    before = list(Inventory.objects.values("id", "quantity", "updated_at"))
    item, _ = services.add_item(user=customer, variant_id=variant.pk, quantity=5)
    services.update_item_quantity(user=customer, item_id=item.pk, quantity=9)
    services.remove_item(user=customer, item_id=item.pk)
    services.clear_cart(user=customer)
    assert list(Inventory.objects.values("id", "quantity", "updated_at")) == before


@pytest.mark.parametrize("operation", ["add", "update", "remove", "clear"])
def test_write_and_cart_timestamp_roll_back_together(
    customer, cart_variant_factory, monkeypatch, operation
):
    variant = cart_variant_factory()
    item, _ = services.add_item(user=customer, variant_id=variant.pk, quantity=1)
    cart = Cart.objects.get(user=customer)
    before = cart.updated_at
    original_touch = services._touch

    def fail(cart):
        original_touch(cart)
        raise RuntimeError("Injected failure after item SQL")

    monkeypatch.setattr(services, "_touch", fail)
    with pytest.raises(RuntimeError):
        if operation == "add":
            services.add_item(user=customer, variant_id=variant.pk, quantity=2)
        elif operation == "update":
            services.update_item_quantity(user=customer, item_id=item.pk, quantity=3)
        elif operation == "remove":
            services.remove_item(user=customer, item_id=item.pk)
        else:
            services.clear_cart(user=customer)
    item.refresh_from_db()
    cart.refresh_from_db()
    assert item.quantity == 1 and cart.updated_at == before


def test_first_add_rollback_removes_new_cart_too(
    customer, cart_variant_factory, monkeypatch
):
    variant = cart_variant_factory()

    def fail(cart):
        raise RuntimeError("Injected failure")

    monkeypatch.setattr(services, "_touch", fail)
    with pytest.raises(RuntimeError):
        services.add_item(user=customer, variant_id=variant.pk, quantity=1)
    assert Cart.objects.count() == 0 and CartItem.objects.count() == 0
