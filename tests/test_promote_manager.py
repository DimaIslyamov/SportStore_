from datetime import datetime, timezone
from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
def test_promote_manager_changes_only_role_and_updated_at():
    User = get_user_model()
    user = User.objects.create_user(
        email="manager@example.com",
        password="existing-test-password",
        phone="+48123456789",
        first_name="Existing",
        last_name="Customer",
    )
    User.objects.filter(pk=user.pk).update(
        updated_at=datetime(2001, 1, 1, tzinfo=timezone.utc)
    )
    before = User.objects.values().get(pk=user.pk)
    output = StringIO()

    call_command("promote_manager", " MANAGER@Example.COM ", stdout=output)

    after = User.objects.values().get(pk=user.pk)
    assert after["role"] == "MANAGER"
    assert after["updated_at"] > before["updated_at"]
    for field, value in before.items():
        if field not in {"role", "updated_at"}:
            assert after[field] == value
    assert "MANAGER" in output.getvalue()


@pytest.mark.django_db
def test_promote_manager_rejects_inactive_user_without_changes():
    User = get_user_model()
    user = User.objects.create_user(email="inactive@example.com", is_active=False)
    before = User.objects.values().get(pk=user.pk)

    with pytest.raises(CommandError, match="inactive user"):
        call_command("promote_manager", user.email)

    assert User.objects.values().get(pk=user.pk) == before


@pytest.mark.django_db
def test_promote_manager_does_not_create_missing_user():
    User = get_user_model()

    with pytest.raises(CommandError, match="not found"):
        call_command("promote_manager", "missing@example.com")

    assert User.objects.count() == 0
