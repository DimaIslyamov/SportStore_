from rest_framework import serializers

from core.serializers import StrictInputMixin

from .models import Payment


class PaymentInputSerializer(StrictInputMixin, serializers.Serializer):
    outcome = serializers.ChoiceField(
        choices=Payment.FakeOutcome.choices, default=Payment.FakeOutcome.SUCCESS
    )


class PaymentOutputSerializer(serializers.ModelSerializer):
    amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, coerce_to_string=True, read_only=True
    )

    class Meta:
        model = Payment
        fields = (
            "id",
            "order",
            "status",
            "provider",
            "external_id",
            "amount",
            "currency",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields
