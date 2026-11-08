from django.core.management.base import BaseCommand, CommandError

from orders.idempotency import cleanup_expired_checkout_keys


class Command(BaseCommand):
    help = "Recover abandoned checkout keys and purge expired results."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=500)

    def handle(self, *args, **options):
        size = options["batch_size"]

        if not 1 <= size <= 10000:
            raise CommandError("--batch-size must be between 1 and 10000")

        recovered, deleted = cleanup_expired_checkout_keys(batch_size=size)
        self.stdout.write(f"Recovered: {recovered}. Deleted: {deleted}.")
        