from django.db import migrations


def normalize_user_emails(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    alias = schema_editor.connection.alias
    rows = list(
        User.objects.using(alias)
        .select_for_update()
        .order_by("pk")
        .values_list("pk", "email")
    )
    seen = {}

    for user_id, email in rows:
        normalized = email.strip().lower()
        if not normalized:
            raise RuntimeError(
                f"User {user_id} has an empty email. Fix it before migration."
            )
        if normalized in seen:
            raise RuntimeError(
                f"Users {seen[normalized]} and {user_id} have conflicting emails. "
                "Resolve the conflict before migration."
            )
        seen[normalized] = user_id

    for user_id, email in rows:
        normalized = email.strip().lower()
        if email != normalized:
            User.objects.using(alias).filter(pk=user_id).update(email=normalized)


class Migration(migrations.Migration):
    dependencies = [
        (
            "accounts",
            "0002_user_created_at_user_phone_user_role_user_updated_at_and_more",
        ),
    ]

    operations = [
        migrations.RunPython(
            normalize_user_emails,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
