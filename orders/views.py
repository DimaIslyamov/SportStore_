from rest_framework import generics
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle

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
    RETRY_AFTER_RESPONSE,
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
        summary="Check out the cart idempotently",
        description=(
            "Requires CUSTOMER, a UUID Idempotency-Key, and the ID of an address "
            "you own. Checkout atomically checks stock, creates the order and "
            "item and address snapshots, decrements inventory, and clears the cart. "
            "The server calculates prices and totals. Reusing the key and payload "
            "returns the stored 201 and original JSON with a new request ID. "
            "400: EMPTY_CART, INVALID_QUANTITY, or invalid input. "
            "404: ADDRESS_NOT_FOUND. 409: OUT_OF_STOCK, PRODUCT_UNAVAILABLE, "
            "CHECKOUT_DATA_INVALID, AMOUNT_OUT_OF_RANGE, IDEMPOTENCY_KEY_REUSED, "
            "IDEMPOTENCY_IN_PROGRESS. 503: IDEMPOTENCY_RESERVATION_BUSY or "
            "IDEMPOTENCY_ATTEMPT_ABORTED. Replaying a FAILED checkout returns the "
            "stored error. Use a new key for a new checkout attempt."
            " Throttle scope checkout: 10 requests per minute per authenticated customer. "
            "A replay can receive 429 before the stored result is returned. "
            "Wait for Retry-After, then resend the original key and payload."
        ),
        parameters=[IDEMPOTENCY_KEY_HEADER, RETRY_AFTER_RESPONSE],
        request=CheckoutInputSerializer,
        responses={
            201: CheckoutOrderOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            409: ERROR_RESPONSES[409],
            415: ERROR_RESPONSES[415],
            503: ERROR_RESPONSES[503],
            **AUTH_ERRORS,
            429: ERROR_RESPONSES[429],
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Checkout using your own address",
                value={"address_id": 7},
                request_only=True,
            ),
            OpenApiExample(
                "Insufficient stock",
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
                "Invalid idempotency key UUID",
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "checkout"
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
        summary="List accessible orders without pagination",
        description=(
            "CUSTOMER can see their own orders. MANAGER and superuser can see all orders. "
            "Returns an array of OrderSummary objects without items or shipping_address."
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
        summary="Get an order with its saved snapshots",
        description=(
            "CUSTOMER can read their own orders. MANAGER and superuser can read any order. "
            "Another customer's order returns 404 ORDER_NOT_FOUND. "
            "shipping_address and items contain historical snapshots."
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
        summary="Cancel your own PENDING or PAID order",
        description=(
            "Requires the CUSTOMER who owns the order. Send an empty JSON object "
            "or an empty body. Returns OrderSummary. Stock is restored once. "
            "Repeating cancellation for a CANCELLED order returns 200 without "
            "restoring stock again. 409: ORDER_CANNOT_BE_CANCELLED or "
            "INVENTORY_RESTORE_UNAVAILABLE. A PROCESSING order cannot be cancelled. "
            "Order cancellation does not refund a simulated payment."
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
        summary="Apply an allowed order status transition",
        description=(
            "Requires MANAGER or superuser and the status field. "
            "Transitions: PENDING → PAID/CANCELLED, PAID → PROCESSING/CANCELLED, "
            "PROCESSING → SHIPPED; SHIPPED → DELIVERED. "
            "DELIVERED and CANCELLED are terminal. Transitioning to PAID requires "
            "a matching SUCCESS payment record. "
            "409: INVALID_ORDER_STATUS_TRANSITION, PAYMENT_CONFIRMATION_REQUIRED, "
            "INVENTORY_RESTORE_UNAVAILABLE. Returns OrderSummary."
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
                "Move a paid order to processing",
                value={"status": "PROCESSING"},
                request_only=True,
            ),
            OpenApiExample(
                "No confirmed payment",
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
