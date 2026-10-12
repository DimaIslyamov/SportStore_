from core.serializers import StrictInputMixin

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import (
    TokenObtainPairSerializer,
    TokenRefreshSerializer,
)
from rest_framework_simplejwt.settings import api_settings
from rest_framework_simplejwt.tokens import RefreshToken


from .services import EmailAlreadyExists, register_customer
from .models import Address


User = get_user_model()


class UserProfileSerializer(StrictInputMixin, serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "email",
            "role",
            "is_active",
            "created_at",
            "updated_at",
        )


class RegisterSerializer(StrictInputMixin, serializers.ModelSerializer):
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        max_length=128,
        trim_whitespace=False,
    )
    password_confirm = serializers.CharField(
        write_only=True,
        max_length=128,
        trim_whitespace=False,
    )

    class Meta:
        model = User
        fields = (
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "phone",
        )

    def validate_email(self, value):
        value = User.objects.normalize_email(value)
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email is already registered.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError(
                {"password_confirm": ["Passwords do not match."]}
            )

        candidate = User(
            email=attrs["email"],
            first_name=attrs.get("first_name", ""),
            last_name=attrs.get("last_name", ""),
        )
        try:
            validate_password(attrs["password"], user=candidate)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc

        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        try:
            return register_customer(**validated_data)
        except EmailAlreadyExists as exc:
            raise serializers.ValidationError({"email": [str(exc)]}) from exc


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    JWT-сериализатор для авторизации пользователя по email и паролю.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"] = serializers.EmailField(
            max_length=254,
            write_only=True,
        )
        self.fields["password"] = serializers.CharField(
            write_only=True,
            max_length=128,
            trim_whitespace=False,
        )

    def validate(self, attrs):
        attrs["email"] = User.objects.normalize_email(attrs["email"])
        return super().validate(attrs)


class AccountTokenRefreshSerializer(TokenRefreshSerializer):
    refresh = serializers.CharField(
        write_only=True,
        max_length=4096,
        trim_whitespace=False,
    )

    def validate(self, attrs):
        try:
            return super().validate(attrs)
        except User.DoesNotExist as exc:
            raise AuthenticationFailed(
                "No active account found for the given token.",
                code="no_active_account",
            ) from exc


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(
        write_only=True,
        max_length=4096,
        trim_whitespace=False,
    )

    def validate_refresh(self, value):
        try:
            token = RefreshToken(value)
        except TokenError as exc:
            raise serializers.ValidationError(
                "Invalid, expired or blacklisted refresh token."
            ) from exc

        user_id = token.get(api_settings.USER_ID_CLAIM)

        if str(user_id) != str(self.context["request"].user.pk):
            raise PermissionDenied("This refresh token belongs to another user.")

        return token

    def save(self, **kwargs):
        self.validated_data["refresh"].blacklist()


class AddressSerializer(StrictInputMixin, serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = (
            "id",
            "user",
            "first_name",
            "last_name",
            "phone",
            "country",
            "city",
            "postal_code",
            "street",
            "building",
            "apartment",
            "is_default",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "user", "created_at", "updated_at")
        # Default switching is handled atomically by the viewset.
        validators = []
