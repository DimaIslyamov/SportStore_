from rest_framework import generics
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
    extend_schema_view,
)

from accounts.permissions import IsCustomer, IsManager
from core.schema import (
    AUTH_ERRORS,
    COMMON_ERRORS,
    ERROR_RESPONSES,
    IDEMPOTENCY_KEY_HEADER,
    REQUEST_ID_RESPONSE,
)

from . import services
from .idempotency import idempotent_checkout, parse_idempotency_key
from .models import Order
from .querysets import checkout_order_queryset, get_order_or_404
from .serializers import (
    CheckoutInputSerializer,
    CheckoutOrderOutputSerializer,
    OrderCancelInputSerializer,
    OrderStatusInputSerializer,
    OrderSummarySerializer,
)


@extend_schema(tags=["Orders"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="orders_checkout",
        summary="Оформить корзину с защитой от повторного запроса",
        description=(
            "CUSTOMER. Обязательны Idempotency-Key UUID и address_id своего Address. "
            "Backend атомарно проверяет stock, создаёт snapshots Order/Items/address, "
            "списывает Inventory и очищает Cart. Цена и totals берутся сервером. "
            "Повтор key/payload возвращает сохранённый 201 и исходный JSON, "
            "а не свежий статус заказа. Новый Request ID назначается всегда. "
            "400: EMPTY_CART/INVALID_QUANTITY или validation. "
            "404: ADDRESS_NOT_FOUND. 409: OUT_OF_STOCK, PRODUCT_UNAVAILABLE, "
            "CHECKOUT_DATA_INVALID, AMOUNT_OUT_OF_RANGE, IDEMPOTENCY_KEY_REUSED, "
            "IDEMPOTENCY_IN_PROGRESS. 503: IDEMPOTENCY_RESERVATION_BUSY или "
            "IDEMPOTENCY_ATTEMPT_ABORTED. FAILED replay сохраняет отказ; "
            "для новой бизнес-попытки нужен новый key."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER],
        request=CheckoutInputSerializer,
        responses={
            201: CheckoutOrderOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
            415: ERROR_RESPONSES[415],
            503: ERROR_RESPONSES[503],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Оформление на свой Address",
                value={"address_id": 7},
                request_only=True,
            ),
            OpenApiExample(
                "Недостаточный остаток",
                value={
                    "error": {
                        "code": "OUT_OF_STOCK",
                        "message": "Not enough stock for variant 100.",
                        "details": {},
                        "request_id": "8f413c8d-2975-4ed3-951f-4a8c875491e2",
                    }
                },
                response_only=True,
                status_codes=[409],
            ),
            OpenApiExample(
                "Неправильный UUID header",
                value={
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Invalid input.",
                        "details": {
                            "idempotency_key": ["Idempotency-Key must be a UUID."]
                        },
                        "request_id": "8f413c8d-2975-4ed3-951f-4a8c875491e2",
                    }
                },
                response_only=True,
                status_codes=[400],
            ),
        ],
    ),
)
class CheckoutView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer]
    parser_classes = [JSONParser]
    serializer_class = CheckoutInputSerializer
    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = idempotent_checkout(
            user=request.user,
            address_id=serializer.validated_data["address_id"],
            key=key,
        )

        return Response(result.body, status=result.status_code)


class OrderReadMixin:
    permission_classes = [IsAuthenticated, IsCustomer | IsManager]
    http_method_names = ["get", "head", "options"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset.none()
        if IsManager().has_permission(self.request, self):
            return queryset
        return queryset.filter(user_id=self.request.user.pk)


@extend_schema(tags=["Orders"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="orders_list",
        summary="Получить доступные заказы без пагинации",
        description=(
            "CUSTOMER видит свои заказы; MANAGER и superuser — все. "
            "Ответ — массив OrderSummary, без items/shipping_address."
        ),
        responses={
            200: OrderSummarySerializer(many=True),
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class OrderListView(OrderReadMixin, generics.ListAPIView):
    serializer_class = OrderSummarySerializer
    queryset = Order.objects.all()


@extend_schema(tags=["Orders"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="orders_retrieve",
        summary="Получить текущий заказ со snapshots",
        description=(
            "CUSTOMER — только собственный Order; MANAGER/superuser — любой. "
            "404 ORDER_NOT_FOUND также скрывает чужой заказ от Customer. "
            "shipping_address и items — сохранённые исторические данные."
        ),
        responses={
            200: CheckoutOrderOutputSerializer,
            404: ERROR_RESPONSES[404],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class OrderDetailView(OrderReadMixin, generics.RetrieveAPIView):
    serializer_class = CheckoutOrderOutputSerializer
    queryset = checkout_order_queryset()

    def get_object(self):
        order = get_order_or_404(self.get_queryset(), order_id=self.kwargs["pk"])
        self.check_object_permissions(self.request, order)
        return order


@extend_schema(tags=["Orders"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="orders_cancel",
        summary="Отменить собственный PENDING/PAID заказ",
        description=(
            "CUSTOMER owner. Пустой JSON {} или пустое тело; поля не принимаются. "
            "Возвращает OrderSummary. Stock восстанавливается один раз. "
            "Повтор для CANCELLED даёт 200 без повторного восстановления. "
            "409: ORDER_CANNOT_BE_CANCELLED либо INVENTORY_RESTORE_UNAVAILABLE. "
            "PROCESSING → CANCELLED запрещён. Отмена fake-оплаты не является refund."
        ),
        request={
            "application/json": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        responses={
            200: OrderSummarySerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class OrderCancelView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer]
    parser_classes = [JSONParser]
    serializer_class = OrderCancelInputSerializer
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.cancel_order(user=request.user, order_id=pk)
        return Response(OrderSummarySerializer(order).data)


@extend_schema(tags=["Orders"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    patch=extend_schema(
        operation_id="orders_status_update",
        summary="Выполнить разрешённый переход статуса",
        description=(
            "MANAGER или superuser. Поле status обязательно. "
            "Граф: PENDING → PAID/CANCELLED; PAID → PROCESSING/CANCELLED; "
            "PROCESSING → SHIPPED; SHIPPED → DELIVERED. "
            "DELIVERED/CANCELLED terminal. Для PAID обязателен matching SUCCESS "
            "Payment: вручную подтверждать оплату нельзя. "
            "409: INVALID_ORDER_STATUS_TRANSITION, PAYMENT_CONFIRMATION_REQUIRED, "
            "INVENTORY_RESTORE_UNAVAILABLE. Ответ — OrderSummary."
        ),
        request=OrderStatusInputSerializer,
        responses={
            200: OrderSummarySerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Отправить оплаченный заказ в обработку",
                value={"status": "PROCESSING"},
                request_only=True,
            ),
            OpenApiExample(
                "Нет подтверждённой оплаты",
                value={
                    "error": {
                        "code": "PAYMENT_CONFIRMATION_REQUIRED",
                        "message": (
                            "A successful matching payment is required to mark the order paid."
                        ),
                        "details": {},
                        "request_id": "8f413c8d-2975-4ed3-951f-4a8c875491e2",
                    }
                },
                response_only=True,
                status_codes=[409],
            ),
        ],
    ),
)
class OrderStatusView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsManager]
    parser_classes = [JSONParser]
    serializer_class = OrderStatusInputSerializer
    http_method_names = ["patch", "options"]

    def patch(self, request, pk):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.change_order_status(
            order_id=pk,
            target_status=serializer.validated_data["status"],
        )
        return Response(OrderSummarySerializer(order).data)
