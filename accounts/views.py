from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .models import Address
from .permissions import IsCustomer
from .serializers import (
    AccountTokenRefreshSerializer,
    AddressSerializer,
    EmailTokenObtainPairSerializer,
    LogoutSerializer,
    RegisterSerializer,
    UserProfileSerializer,
)


class RegisterView(generics.GenericAPIView):
    serializer_class = RegisterSerializer
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            UserProfileSerializer(user).data,
            status=status.HTTP_201_CREATED,
        )


class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class AccountTokenRefreshView(TokenRefreshView):
    serializer_class = AccountTokenRefreshSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


class LogoutView(generics.GenericAPIView):
    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated, IsCustomer]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def _lock_owner(self):
        User = get_user_model()
        User.objects.select_for_update().get(pk=self.request.user.pk)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        self._lock_owner()
        return super().create(request, *args, **kwargs)

    def perform_create(self, serializer):
        if serializer.validated_data.get("is_default", False):
            self.get_queryset().filter(is_default=True).update(
                is_default=False,
                updated_at=timezone.now(),
            )
        serializer.save(user=self.request.user)

    @transaction.atomic
    def update(self, request, *args, **kwargs):
        self._lock_owner()
        return super().update(request, *args, **kwargs)

    def perform_update(self, serializer):
        if serializer.validated_data.get("is_default", False):
            (
                self.get_queryset()
                .filter(is_default=True)
                .exclude(pk=serializer.instance.pk)
                .update(is_default=False, updated_at=timezone.now())
            )
        serializer.save()

    @transaction.atomic
    def destroy(self, request, *args, **kwargs):
        self._lock_owner()
        return super().destroy(request, *args, **kwargs)
