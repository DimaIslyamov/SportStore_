from django.db import transaction
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import get_object_or_404

from accounts.permissions import IsManager

from . import services
from .manager_serializers import (
    ImageInputSerializer,
    InventoryInputSerializer,
    InventoryOutputSerializer,
    VariantInputSerializer,
    VariantOutputSerializer,
)
from .models import Inventory, Product, ProductImage, ProductVariant
from .product_serializers import ProductImageSerializer


def input_data(request):
    data = request.data
    return data.dict() if hasattr(data, "dict") else data


class ManagerView(APIView):
    permission_classes = [IsAuthenticated, IsManager]


class VariantCreateView(ManagerView):
    http_method_names = ["post", "options"]

    def post(self, request, product_id):
        get_object_or_404(Product, pk=product_id)
        serializer = VariantInputSerializer(data=input_data(request))
        serializer.is_valid(raise_exception=True)
        variant = services.create_variant(product_id, **serializer.validated_data)
        return Response(
            VariantOutputSerializer(variant).data, status=status.HTTP_201_CREATED
        )


class VariantUpdateView(ManagerView):
    http_method_names = ["patch", "options"]

    def patch(self, request, product_id, variant_id):
        variant = get_object_or_404(
            ProductVariant, pk=variant_id, product_id=product_id
        )
        serializer = VariantInputSerializer(
            variant, data=input_data(request), partial=True
        )
        serializer.is_valid(raise_exception=True)
        updated = services.update_variant(
            product_id, variant_id, **serializer.validated_data
        )
        return Response(VariantOutputSerializer(updated).data)


class InventoryView(ManagerView):
    http_method_names = ["get", "patch", "head", "options"]

    def get(self, request, product_id, variant_id):
        variant = get_object_or_404(
            ProductVariant, pk=variant_id, product_id=product_id
        )
        inventory = get_object_or_404(Inventory, variant=variant)
        return Response(InventoryOutputSerializer(inventory).data)

    def patch(self, request, product_id, variant_id):
        get_object_or_404(ProductVariant, pk=variant_id, product_id=product_id)
        serializer = InventoryInputSerializer(data=input_data(request))
        serializer.is_valid(raise_exception=True)
        inventory = services.update_inventory(
            product_id, variant_id, **serializer.validated_data
        )
        return Response(InventoryOutputSerializer(inventory).data)


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ImageCreateView(ManagerView):
    http_method_names = ["post", "options"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, product_id):
        get_object_or_404(Product, pk=product_id)
        serializer = ImageInputSerializer(data=input_data(request))
        serializer.is_valid(raise_exception=True)
        image = services.create_image(product_id, **serializer.validated_data)
        return Response(
            ProductImageSerializer(image).data, status=status.HTTP_201_CREATED
        )


@method_decorator(transaction.non_atomic_requests, name="dispatch")
class ImageUpdateDeleteView(ManagerView):
    http_method_names = ["patch", "delete", "options"]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def patch(self, request, product_id, image_id):
        image = get_object_or_404(ProductImage, pk=image_id, product_id=product_id)
        serializer = ImageInputSerializer(image, data=input_data(request), partial=True)
        serializer.is_valid(raise_exception=True)
        updated = services.update_image(
            product_id, image_id, **serializer.validated_data
        )
        return Response(ProductImageSerializer(updated).data)

    def delete(self, request, product_id, image_id):
        services.delete_image(product_id, image_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
