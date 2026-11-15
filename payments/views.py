from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

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
)
from orders.idempotency import parse_idempotency_key

from .serializers import PaymentInputSerializer, PaymentOutputSerializer
from .services import create_payment


@extend_schema(tags=["Payments"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="payments_create",
        summary="Создать или повторно получить fake-попытку оплаты",
        description=(
            "CUSTOMER owner. Обязателен Idempotency-Key UUID. "
            "outcome: success/failed, default success; можно отправить {}. "
            "amount/currency/provider задаются сервером из Order и не принимаются. "
            "Новая попытка: 201 с SUCCESS либо FAILED. Replay key/outcome: 200 "
            "с прежним Payment, даже если текущий статус Order уже изменился. "
            "Новый key после SUCCESS: 409 ALREADY_PAID. "
            "Новый key для CANCELLED: 409 ORDER_NOT_PAYABLE. "
            "Изменённый outcome при прежнем key: 409 IDEMPOTENCY_KEY_REUSED. "
            "404 ORDER_NOT_FOUND скрывает чужой заказ. Это синхронный fake, "
            "без настоящих денег, webhook и refund. REFUNDED из enum сейчас "
            "не является реализованной операцией."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER],
        request=PaymentInputSerializer,
        responses={
            200: PaymentOutputSerializer,
            201: PaymentOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Симуляция отказа",
                value={"outcome": "failed"},
                request_only=True,
            ),
            OpenApiExample(
                "Созданная неуспешная попытка",
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
