from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from .managers import UserManager


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        MANAGER = "MANAGER", "Manager"

    username = None
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=32, blank=True, default="")
    role = models.CharField(
        max_length=16,
        choices=Role.choices,
        default=Role.CUSTOMER,
        db_index=True,
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        abstract = False
        constraints = [
            models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=["CUSTOMER", "MANAGER"]),
                name="accounts_user_role_valid",
            ),
        ]

    def save(self, *args, **kwargs):
        self.email = type(self).objects.normalize_email(self.email)
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.email
