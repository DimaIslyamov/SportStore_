from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction


class EmailAlreadyExists(Exception):
    pass


def register_customer(
    *,
    email,
    password,
    first_name="",
    last_name="",
    phone="",
):
    User = get_user_model()
    email = User.objects.normalize_email(email)

    try:
        with transaction.atomic():
            return User.objects.create_user(
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                phone=phone,
                role=User.Role.CUSTOMER,
                is_staff=False,
                is_superuser=False,
                is_active=True,
            )
    except IntegrityError as exc:
        if User.objects.filter(email__iexact=email).exists():
            raise EmailAlreadyExists("Email is already registered.") from exc
        raise
