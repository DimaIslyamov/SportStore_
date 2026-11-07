from django.db.models import Prefetch

from cart.models import CartItem

from .models import Order, OrderItem


def cart_items_for_checkout(*, cart):
    return CartItem.objects.filter(cart=cart).order_by("variant_id", "pk")


def checkout_order_queryset():
    return Order.objects.select_related("shipping_address").prefetch_related(
        Prefetch(
            "items",
            queryset=OrderItem.objects.order_by("pk"),
            to_attr="loaded_items",
        ),
    )
