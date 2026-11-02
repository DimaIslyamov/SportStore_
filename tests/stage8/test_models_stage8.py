import pytest
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError

from cart.models import Cart, CartItem

pytestmark = pytest.mark.django_db


def test_only_one_cart_for_user(customer):
    Cart.objects.create(user=customer)
    with pytest.raises(IntegrityError), transaction.atomic():
        Cart.objects.create(user=customer)
    assert Cart.objects.filter(user=customer).count() == 1


def test_pair_cart_variant_is_unique(customer, cart_variant_factory):
    cart = Cart.objects.create(user=customer)
    variant = cart_variant_factory()
    CartItem.objects.create(cart=cart, variant=variant, quantity=1)
    with pytest.raises(IntegrityError), transaction.atomic():
        CartItem.objects.create(cart=cart, variant=variant, quantity=2)
    assert cart.items.count() == 1


@pytest.mark.parametrize("quantity", [0, -1, 1000])
def test_sql_rejects_invalid_quantity(customer, cart_variant_factory, quantity):
    cart = Cart.objects.create(user=customer)
    variant = cart_variant_factory()
    with pytest.raises(IntegrityError), transaction.atomic():
        CartItem.objects.create(cart=cart, variant=variant, quantity=quantity)
    assert not cart.items.exists()


def test_variant_is_protected_and_user_delete_cascades_cart(
    customer, cart_variant_factory
):
    cart = Cart.objects.create(user=customer)
    variant = cart_variant_factory(stock=5)
    item = CartItem.objects.create(cart=cart, variant=variant, quantity=1)
    with pytest.raises(ProtectedError):
        variant.delete()
    assert CartItem.objects.filter(pk=item.pk).exists()
    cart_id = cart.pk
    customer.delete()
    assert not Cart.objects.filter(pk=cart_id).exists()
    assert not CartItem.objects.filter(pk=item.pk).exists()
    variant.refresh_from_db()
