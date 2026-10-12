from rest_framework.permissions import BasePermission

from .models import User


class IsCustomer(BasePermission):
    message = "Customer role is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user.is_authenticated and user.is_active and user.role == User.Role.CUSTOMER
        )


class IsManager(BasePermission):
    message = "Manager role is required."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user.is_authenticated
            and user.is_active
            and (user.role == User.Role.MANAGER or user.is_superuser)
        )
