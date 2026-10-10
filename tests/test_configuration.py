from django.contrib.auth import get_user_model
from django.db import connection

from accounts.models import User


def test_project_uses_accounts_user():
    assert get_user_model() is User
    assert User.USERNAME_FIELD == "email"


def test_project_uses_postgresql_backend():
    assert connection.vendor == "postgresql"
