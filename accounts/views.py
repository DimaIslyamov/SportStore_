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
from drf_spectacular.utils import OpenApiExample, extend_schema, extend_schema_view

from core.schema import (
    AUTH_ERRORS,
    COMMON_ERRORS,
    ERROR_RESPONSES,
    REQUEST_ID_RESPONSE,
    TokenPairOutputSerializer,
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


@extend_schema(tags=["Authentication"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="auth_register",
        summary="Зарегистрировать Customer",
        description=(
            "Создаётся CUSTOMER. role, is_staff и другие read-only/неизвестные "
            "поля запрещены StrictInputMixin. Пароли в ответ не возвращаются."
        ),
        request=RegisterSerializer,
        responses={
            201: UserProfileSerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Запрещённое поле role",
                value={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid input.",
                        "details": {"role": ["This field cannot be submitted."]},
                        "request_id": "8f413c8d-2975-4ed3-951f-4a8c875491e2",
                    }
                },
                response_only=True,
                status_codes=[400],
            ),
        ],
    ),
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


@extend_schema(tags=["Authentication"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="auth_token_create",
        summary="Получить access и refresh по email/password",
        request=EmailTokenObtainPairSerializer,
        responses={
            200: TokenPairOutputSerializer,
            400: ERROR_RESPONSES[400],
            401: ERROR_RESPONSES[401],
            415: ERROR_RESPONSES[415],
            **COMMON_ERRORS,
        },
    ),
)
class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


@extend_schema(tags=["Authentication"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="auth_token_refresh",
        summary="Обновить JWT и получить новую refresh-пару",
        description=(
            "ROTATE_REFRESH_TOKENS=True: ответ содержит access и refresh. "
            "Старый refresh попадает в blacklist; сохрани новый refresh."
        ),
        request=AccountTokenRefreshSerializer,
        responses={
            200: TokenPairOutputSerializer,
            400: ERROR_RESPONSES[400],
            401: ERROR_RESPONSES[401],
            415: ERROR_RESPONSES[415],
            **COMMON_ERRORS,
        },
    ),
)
class AccountTokenRefreshView(TokenRefreshView):
    serializer_class = AccountTokenRefreshSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


@extend_schema(tags=["Authentication"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="auth_logout",
        summary="Заблокировать собственный refresh token",
        description=(
            "Нужны access JWT и refresh этого пользователя в JSON. "
            "204 не содержит JSON-тела. Чужой refresh даёт 403."
        ),
        request=LogoutSerializer,
        responses={
            204: None,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class LogoutView(generics.GenericAPIView):
    serializer_class = LogoutSerializer
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(tags=["Users"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="users_me_retrieve",
        summary="Получить собственный профиль",
        responses={200: UserProfileSerializer, **AUTH_ERRORS, **COMMON_ERRORS},
    ),
    patch=extend_schema(
        operation_id="users_me_update",
        summary="Частично изменить собственный профиль",
        description=(
            "Можно передавать first_name, last_name, phone. "
            "email, role и прочие read-only/неизвестные поля запрещены."
        ),
        request=UserProfileSerializer,
        responses={
            200: UserProfileSerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserProfileSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


@extend_schema(
    tags=["Addresses"],
    parameters=[REQUEST_ID_RESPONSE],
    description=(
        "Требуется CUSTOMER. Доступны только собственные адреса; чужой ID даёт 404. "
        "user определяется сервером. is_default переключается атомарно."
    ),
)
@extend_schema_view(
    list=extend_schema(
        operation_id="addresses_list",
        summary="Получить свои адреса без пагинации",
        responses={200: AddressSerializer(many=True), **AUTH_ERRORS, **COMMON_ERRORS},
    ),
    create=extend_schema(
        operation_id="addresses_create",
        request=AddressSerializer,
        responses={
            201: AddressSerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
    retrieve=extend_schema(
        operation_id="addresses_retrieve",
        responses={
            200: AddressSerializer,
            404: ERROR_RESPONSES[404],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
    partial_update=extend_schema(
        operation_id="addresses_update",
        request=AddressSerializer,
        responses={
            200: AddressSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
    destroy=extend_schema(
        operation_id="addresses_delete",
        request=None,
        responses={
            204: None,
            404: ERROR_RESPONSES[404],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated, IsCustomer]
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
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
