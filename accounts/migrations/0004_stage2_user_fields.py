from django.db import migrations, models


def backfill_user_metadata(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    alias = schema_editor.connection.alias
    users = User.objects.using(alias)

    users.update(
        created_at=models.F("date_joined"),
        updated_at=models.F("date_joined"),
    )
    users.filter(is_superuser=True).update(role="MANAGER")


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_normalize_user_emails"),
    ]

    operations = [
        migrations.RunPython(
            backfill_user_metadata,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
