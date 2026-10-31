from rest_framework.permissions import BasePermission

from .models import Cart, CartItem


class IsCartOwner(BasePermission):
    message = "This cart belongs to another user."

    def has_object_permission(self, request, view, obj):
        if isinstance(obj, Cart):
            return obj.user_id == request.user.pk
        if isinstance(obj, CartItem):
            return obj.cart.user_id == request.user.pk
        return False
