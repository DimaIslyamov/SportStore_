from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

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
    REQUEST_ID_RESPONSE,
)

from . import services
from .models import Cart, CartItem
from .permissions import IsCartOwner
from .querysets import cart_queryset
from .serializers import (
    CartItemAddInputSerializer,
    CartItemOutputSerializer,
    CartItemQuantityInputSerializer,
    CartOutputSerializer,
)


@extend_schema(tags=["Cart"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="cart_retrieve",
        summary="Получить собственную корзину",
        description=(
            "Требуется CUSTOMER. GET не создаёт Cart в БД. "
            "Если сохранённой корзины нет, id/created_at/updated_at равны null. "
            "Cart не резервирует Inventory; суммы рассчитывает backend."
        ),
        responses={200: CartOutputSerializer, **AUTH_ERRORS, **COMMON_ERRORS},
    ),
)
class CartView(APIView):
    permission_classes = [IsAuthenticated, IsCustomer, IsCartOwner]
    parser_classes = [JSONParser]
    http_method_names = ["get", "head", "options"]

    def get(self, request):
        prepared = cart_queryset().filter(user=request.user).first()
        if prepared is None:
            prepared = Cart(user=request.user)
            prepared.loaded_items = []
        self.check_object_permissions(request, prepared)
        return Response(CartOutputSerializer(prepared).data)


@extend_schema(tags=["Cart"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="cart_items_create",
        summary="Добавить Variant или увеличить quantity существующей позиции",
        description=(
            "Требуется CUSTOMER. variant — ID, quantity от 1 до 999. "
            "201: новая строка; 200: увеличение прежней строки. "
            "Цена не принимается от клиента. Stock повторно проверит checkout."
        ),
        request=CartItemAddInputSerializer,
        responses={
            200: CartItemOutputSerializer,
            201: CartItemOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
        examples=[
            OpenApiExample(
                "Добавить две единицы Variant",
                value={"variant": 100, "quantity": 2},
                request_only=True,
            ),
        ],
    ),
)
class CartItemCreateView(APIView):
    permission_classes = [IsAuthenticated, IsCustomer, IsCartOwner]
    parser_classes = [JSONParser]
    http_method_names = ["post", "options"]

    def post(self, request):
        serializer = CartItemAddInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item, created = services.add_item(
            user=request.user,
            variant_id=serializer.validated_data["variant"],
            quantity=serializer.validated_data["quantity"],
        )
        self.check_object_permissions(request, item)
        return Response(
            CartItemOutputSerializer(item).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


@extend_schema(tags=["Cart"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    patch=extend_schema(
        operation_id="cart_items_update",
        summary="Установить quantity своей позиции",
        description=(
            "Требуется CUSTOMER. quantity обязателен: целое от 1 до 999. "
            "Это абсолютное количество. Чужой CartItem скрыт за 404."
        ),
        request=CartItemQuantityInputSerializer,
        responses={
            200: CartItemOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
    delete=extend_schema(
        operation_id="cart_items_delete",
        summary="Удалить свою позицию",
        request=None,
        responses={
            204: None,
            404: ERROR_RESPONSES[404],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class CartItemDetailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer, IsCartOwner]
    parser_classes = [JSONParser]
    http_method_names = ["patch", "delete", "options"]
    serializer_class = CartItemQuantityInputSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return CartItem.objects.none()
        return CartItem.objects.filter(cart__user=self.request.user).select_related(
            "cart"
        )

    def patch(self, request, *args, **kwargs):
        item = self.get_object()
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = services.update_item_quantity(
            user=request.user,
            item_id=item.pk,
            quantity=serializer.validated_data["quantity"],
        )
        return Response(CartItemOutputSerializer(updated).data)

    def delete(self, request, *args, **kwargs):
        item = self.get_object()
        services.remove_item(user=request.user, item_id=item.pk)

        return Response(status=status.HTTP_204_NO_CONTENT)
