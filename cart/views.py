from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsCustomer

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


class CartItemDetailView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer, IsCartOwner]
    parser_classes = [JSONParser]
    http_method_names = ["patch", "delete", "options"]
    serializer_class = CartItemQuantityInputSerializer

    def get_queryset(self):
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
