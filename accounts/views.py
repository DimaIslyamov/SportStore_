from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from rest_framework import generics, status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
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
    RETRY_AFTER_RESPONSE,
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
        summary="Register a customer",
        description=(
            "Creates a CUSTOMER. StrictInputMixin rejects role, is_staff, and other "
            "read-only or unknown fields. The response omits passwords."
            " Throttle scope auth_register: 5 requests per hour per IP address."
        ),
        parameters=[RETRY_AFTER_RESPONSE],
        request=RegisterSerializer,
        responses={
            201: UserProfileSerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            429: ERROR_RESPONSES[429],
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Protected role field",
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"
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
        summary="Obtain access and refresh tokens using email and password",
        description="Throttle scope auth_login: 5 requests per minute per IP address.",
        parameters=[RETRY_AFTER_RESPONSE],
        request=EmailTokenObtainPairSerializer,
        responses={
            200: TokenPairOutputSerializer,
            400: ERROR_RESPONSES[400],
            401: ERROR_RESPONSES[401],
            415: ERROR_RESPONSES[415],
            429: ERROR_RESPONSES[429],
            **COMMON_ERRORS,
        },
    ),
)
class EmailTokenObtainPairView(TokenObtainPairView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_login"
    serializer_class = EmailTokenObtainPairSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


@extend_schema(tags=["Authentication"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="auth_token_refresh",
        summary="Rotate the refresh token and issue a new token pair",
        description=(
            "With ROTATE_REFRESH_TOKENS=True, the response includes access and refresh. "
            "The old refresh token is blacklisted. Store the new refresh token."
            " Throttle scope auth_refresh: 30 requests per minute per IP address."
        ),
        parameters=[RETRY_AFTER_RESPONSE],
        request=AccountTokenRefreshSerializer,
        responses={
            200: TokenPairOutputSerializer,
            400: ERROR_RESPONSES[400],
            401: ERROR_RESPONSES[401],
            415: ERROR_RESPONSES[415],
            429: ERROR_RESPONSES[429],
            **COMMON_ERRORS,
        },
    ),
)
class AccountTokenRefreshView(TokenRefreshView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_refresh"
    serializer_class = AccountTokenRefreshSerializer
    authentication_classes = []
    permission_classes = [AllowAny]


@extend_schema(tags=["Authentication"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="auth_logout",
        summary="Blacklist your own refresh token",
        description=(
            "Requires a Bearer access token and the same user's refresh token in JSON. "
            "A 204 response has an empty body. Another user's refresh token returns 403."
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
        summary="Get your profile",
        responses={200: UserProfileSerializer, **AUTH_ERRORS, **COMMON_ERRORS},
    ),
    patch=extend_schema(
        operation_id="users_me_update",
        summary="Partially update your profile",
        description=(
            "Accepts first_name, last_name, and phone. "
            "Rejects email, role, and other read-only or unknown fields."
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
        "Requires CUSTOMER. Only your own addresses are accessible. "
        "Another user's address ID returns 404. The server sets user and updates "
        "is_default atomically."
    ),
)
@extend_schema_view(
    list=extend_schema(
        operation_id="addresses_list",
        summary="List your addresses without pagination",
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
