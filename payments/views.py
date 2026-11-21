from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    extend_schema_view,
)

from accounts.permissions import IsCustomer
from core.schema import (
    AUTH_ERRORS,
    COMMON_ERRORS,
    ERROR_RESPONSES,
    IDEMPOTENCY_KEY_HEADER,
    REQUEST_ID_RESPONSE,
    RETRY_AFTER_RESPONSE,
)
from orders.idempotency import parse_idempotency_key

from .serializers import PaymentInputSerializer, PaymentOutputSerializer
from .services import create_payment


@extend_schema(tags=["Payments"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="payments_create",
        summary="Create or replay a simulated payment attempt",
        description=(
            "Requires the CUSTOMER who owns the order and a UUID Idempotency-Key. "
            "outcome accepts success or failed and defaults to success for {}. "
            "The server sets amount and currency from the order and selects the provider. "
            "A new attempt returns 201 with SUCCESS or FAILED. Reusing the key and "
            "outcome returns 200 with the original payment even after the order status changes. "
            "A new key after SUCCESS returns 409 ALREADY_PAID. A new key for a "
            "CANCELLED order returns 409 ORDER_NOT_PAYABLE. Changing outcome for the "
            "same key returns 409 IDEMPOTENCY_KEY_REUSED. Another customer's order "
            "returns 404 ORDER_NOT_FOUND. The provider simulates payments synchronously "
            "without real money, webhooks, or refunds."
            " Throttle scope payment: 10 requests per minute per authenticated customer. "
            "A replay can receive 429 before the stored payment is returned. "
            "Wait for Retry-After, then resend the original key and payload."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, RETRY_AFTER_RESPONSE],
        request=PaymentInputSerializer,
        responses={
            200: PaymentOutputSerializer,
            201: PaymentOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            429: ERROR_RESPONSES[429],
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Simulate a declined payment",
                value={"outcome": "failed"},
                request_only=True,
            ),
            OpenApiExample(
                "Recorded failed payment attempt",
                value={
                    "id": 21,
                    "order": 42,
                    "status": "FAILED",
                    "provider": "FAKE",
                    "external_id": "fake_0123456789abcdef0123456789abcdef",
                    "amount": "39.80",
                    "currency": "PLN",
                    "created_at": "2026-11-14T10:00:00Z",
                    "updated_at": "2026-11-14T10:00:00Z",
                },
                response_only=True,
                status_codes=[201],
            ),
        ],
    ),
)
class OrderPaymentView(generics.GenericAPIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "payment"
    permission_classes = [IsAuthenticated, IsCustomer]
    parser_classes = [JSONParser]
    serializer_class = PaymentInputSerializer
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = create_payment(
            user=request.user,
            order_id=pk,
            key=key,
            outcome=serializer.validated_data["outcome"],
        )
        return Response(
            PaymentOutputSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
