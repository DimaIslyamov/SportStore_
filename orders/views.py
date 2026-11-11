from rest_framework import generics
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsCustomer, IsManager

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
        if IsManager().has_permission(self.request, self):
            return queryset
        return queryset.filter(user_id=self.request.user.pk)


class OrderListView(OrderReadMixin, generics.ListAPIView):
    serializer_class = OrderSummarySerializer
    queryset = Order.objects.all()


class OrderDetailView(OrderReadMixin, generics.RetrieveAPIView):
    serializer_class = CheckoutOrderOutputSerializer
    queryset = checkout_order_queryset()

    def get_object(self):
        order = get_order_or_404(self.get_queryset(), order_id=self.kwargs["pk"])
        self.check_object_permissions(self.request, order)
        return order


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
