from uuid import uuid4

from django.utils import timezone


def generate_order_number():
    return f"SP-{timezone.now().year}-{uuid4().hex}"
