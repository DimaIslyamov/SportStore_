from importlib import import_module

import django.utils.timezone
from django.db import migrations, models
from django.db.models.functions import Lower


normalize_user_emails = import_module(
    "accounts.migrations.0003_normalize_user_emails"
).normalize_user_emails
backfill_user_metadata = import_module(
    "accounts.migrations.0004_stage2_user_fields"
).backfill_user_metadata


class Migration(migrations.Migration):
    # Keep the originals: databases with 0002 applied must finish that path.
    replaces = [
        (
            "accounts",
            "0002_user_created_at_user_phone_user_role_user_updated_at_and_more",
        ),
        ("accounts", "0003_normalize_user_emails"),
        ("accounts", "0004_stage2_user_fields"),
    ]

    dependencies = [
        ("accounts", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="phone",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[("CUSTOMER", "Customer"), ("MANAGER", "Manager")],
                db_index=True,
                default="CUSTOMER",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="created_at",
            field=models.DateTimeField(
                default=django.utils.timezone.now,
                editable=False,
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="updated_at",
            field=models.DateTimeField(
                auto_now=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.RunPython(
            normalize_user_emails,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.RunPython(
            backfill_user_metadata,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.UniqueConstraint(
                Lower("email"),
                name="accounts_user_email_ci_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=models.Q(role__in=["CUSTOMER", "MANAGER"]),
                name="accounts_user_role_valid",
            ),
        ),
    ]
