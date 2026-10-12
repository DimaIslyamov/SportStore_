from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Assign MANAGER role to an existing active user."

    def add_arguments(self, parser):
        parser.add_argument("email")

    def handle(self, *args, **options):
        User = get_user_model()
        email = User.objects.normalize_email(options["email"])

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist as exc:
            raise CommandError("User was not found.") from exc

        if not user.is_active:
            raise CommandError("Cannot promote an inactive user.")

        user.role = User.Role.MANAGER
        user.save(update_fields=["role", "updated_at"])

        self.stdout.write(self.style.SUCCESS(f"User {user.pk} is now MANAGER."))
