from django.db.models import Prefetch

from .models import Cart, CartItem


def cart_item_queryset():
    return CartItem.objects.select_related(
        "cart",
        "variant__product__category",
        "variant__product__brand",
        "variant__inventory",
    ).order_by("id")


def cart_queryset():
    return Cart.objects.prefetch_related(
        Prefetch("items", queryset=cart_item_queryset(), to_attr="loaded_items")
    )
