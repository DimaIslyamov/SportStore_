from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction


@pytest.mark.django_db
def test_customer_defaults_and_timestamps(raw_password):
    User = get_user_model()

    user = User.objects.create_user(
        email=" Mixed.Case@Example.COM ",
        password=raw_password,
    )

    assert user.email == "mixed.case@example.com"
    assert user.role == User.Role.CUSTOMER
    assert user.is_staff is False
    assert user.is_superuser is False
    assert user.phone == ""
    assert user.created_at is not None
    assert user.updated_at is not None
    assert user.check_password(raw_password)


@pytest.mark.parametrize("email", [None, "", "   "])
def test_manager_rejects_empty_email(email):
    User = get_user_model()

    with pytest.raises(ValueError, match="Email is required"):
        User.objects.create_user(email=email, password="unused-password")


@pytest.mark.parametrize("flag", ["is_staff", "is_superuser", "is_active"])
def test_superuser_rejects_disabled_required_flag(flag):
    User = get_user_model()

    with pytest.raises(ValueError):
        User.objects.create_superuser(
            email="admin@example.com",
            password="unused-password",
            **{flag: False},
        )


@pytest.mark.django_db
def test_database_rejects_case_insensitive_email_duplicate(customer):
    User = get_user_model()
    other = User.objects.create_user(email="other@example.com", password=None)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.filter(pk=other.pk).update(email="CUSTOMER@EXAMPLE.COM")


@pytest.mark.django_db
def test_database_rejects_unknown_role(customer):
    User = get_user_model()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            User.objects.filter(pk=customer.pk).update(role="OWNER")


@pytest.mark.django_db
def test_command_promotes_without_administrative_flags(customer):
    User = get_user_model()
    output = StringIO()

    call_command("promote_manager", customer.email.upper(), stdout=output)
    customer.refresh_from_db()

    assert customer.role == User.Role.MANAGER
    assert customer.is_staff is False
    assert customer.is_superuser is False
    assert "MANAGER" in output.getvalue()


@pytest.mark.django_db
def test_command_rejects_inactive_user(customer):
    customer.is_active = False
    customer.save(update_fields=["is_active", "updated_at"])

    with pytest.raises(CommandError, match="inactive"):
        call_command("promote_manager", customer.email)


@pytest.mark.django_db
def test_command_rejects_missing_user():
    with pytest.raises(CommandError, match="not found"):
        call_command("promote_manager", "missing@example.com")
