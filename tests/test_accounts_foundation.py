import pytest
from django.contrib.auth import get_user_model


@pytest.mark.django_db
def test_create_user_normalizes_email_and_hashes_password():
    User = get_user_model()

    user = User.objects.create_user(
        email=" Student@Example.COM ",
        password="foundation-test-password",
    )

    user.refresh_from_db()

    assert user.email == "student@example.com"
    assert user.check_password("foundation-test-password")
    assert user.password != "foundation-test-password"
    assert user.is_staff is False
    assert user.is_superuser is False


@pytest.mark.django_db
def test_create_superuser_sets_administrative_flags():
    User = get_user_model()

    user = User.objects.create_superuser(
        email="admin@example.com",
        password="foundation-test-password",
    )

    assert user.is_staff is True
    assert user.is_superuser is True
    assert user.is_active is True


def test_superuser_cannot_disable_staff_flag():
    User = get_user_model()

    with pytest.raises(ValueError, match="is_staff=True"):
        User.objects.create_superuser(
            email="admin@example.com",
            password="foundation-test-password",
            is_staff=False,
        )