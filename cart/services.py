from django.db import transaction
from django.http import Http404
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.generics import get_object_or_404

from accounts.models import User
from catalog.models import ProductVariant

from .models import Cart, CartItem
from .querysets import cart_item_queryset
from .rules import MAX_CART_QUANTITY, variant_is_active


def _require_customer(user):
    if not (
        user.is_authenticated and user.is_active and user.role == User.Role.CUSTOMER
    ):
        raise PermissionDenied("Active customer role is required.")


def _validate_quantity(quantity):
    if type(quantity) is not int or not 1 <= quantity <= MAX_CART_QUANTITY:
        raise ValidationError(
            {"quantity": [f"Use an integer from 1 to {MAX_CART_QUANTITY}."]}
        )


def _require_active(variant):
    if not variant_is_active(variant):
        raise ValidationError(
            {"variant": ["This variant is not available for cart changes."]}
        )


def get_or_create_cart(*, user):
    _require_customer(user)
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart


def _locked_cart(user, *, create=False):
    _require_customer(user)

    if create:
        cart = get_or_create_cart(user=user)
        return Cart.objects.select_for_update().get(pk=cart.pk, user=user)

    return Cart.objects.select_for_update().filter(user=user).first()


def _touch(cart):
    cart.save(update_fields=["updated_at"])


def add_item(*, user, variant_id, quantity):
    _validate_quantity(quantity)

    with transaction.atomic():
        cart = _locked_cart(user, create=True)
        variant = get_object_or_404(
            ProductVariant.objects.select_related(
                "product__category",
                "product__brand",
                "inventory",
            ),
            pk=variant_id,
        )

        _require_active(variant)

        item = CartItem.objects.filter(cart=cart, variant=variant).first()
        created = item is None

        if created:
            item = CartItem.objects.create(
                cart=cart, variant=variant, quantity=quantity
            )
        else:
            new_quantity = item.quantity + quantity

            _validate_quantity(new_quantity)

            item.quantity = new_quantity
            item.save(update_fields=["quantity", "updated_at"])
            item.cart = cart
            item.variant = variant
        _touch(cart)

        return item, created


def update_item_quantity(*, user, item_id, quantity):
    _validate_quantity(quantity)

    with transaction.atomic():
        cart = _locked_cart(user)

        if cart is None:
            raise Http404

        item = get_object_or_404(
            cart_item_queryset(),
            pk=item_id,
            cart=cart,
        )
        _require_active(item.variant)
        item.quantity = quantity
        item.save(update_fields=["quantity", "updated_at"])
        _touch(cart)
        return item


def remove_item(*, user, item_id):
    with transaction.atomic():
        cart = _locked_cart(user)

        if cart is None:
            raise Http404

        item = get_object_or_404(CartItem, pk=item_id, cart=cart)
        item.delete()
        _touch(cart)


def clear_cart(*, user):
    with transaction.atomic():
        cart = _locked_cart(user)

        if cart is None:
            return None

        deleted_count, _ = CartItem.objects.filter(cart=cart).delete()

        if deleted_count:
            _touch(cart)

        return cart
