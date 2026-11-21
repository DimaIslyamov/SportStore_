from django.db import transaction
from django.utils.decorators import method_decorator
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.generics import get_object_or_404

from drf_spectacular.utils import extend_schema, extend_schema_view

from accounts.permissions import IsManager
from core.schema import (
    AUTH_ERRORS,
    COMMON_ERRORS,
    ERROR_RESPONSES,
    REQUEST_ID_RESPONSE,
)

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


IMAGE_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "alt_text": {"type": "string", "maxLength": 255},
        "position": {"type": "integer", "minimum": 0, "maximum": 2147483647},
        "is_primary": {"type": "boolean"},
    },
    "additionalProperties": False,
}


def input_data(request):
    data = request.data
    return data.dict() if hasattr(data, "dict") else data


class ManagerView(APIView):
    permission_classes = [IsAuthenticated, IsManager]


@extend_schema(tags=["Manager Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="variants_create",
        summary="Create a variant with zero inventory",
        description="Requires MANAGER or superuser. product_id identifies the product.",
        request=VariantInputSerializer,
        responses={
            201: VariantOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
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


@extend_schema(tags=["Manager Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    patch=extend_schema(
        operation_id="variants_update",
        summary="Partially update a product variant",
        description=(
            "Requires MANAGER or superuser. The SKU is immutable. The variant owns "
            "its price and must belong to the product identified by product_id."
        ),
        request=VariantInputSerializer,
        responses={
            200: VariantOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
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


@extend_schema(tags=["Manager Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="inventory_retrieve",
        summary="Get variant inventory",
        description="Requires MANAGER or superuser. The variant must belong to the product.",
        responses={
            200: InventoryOutputSerializer,
            404: ERROR_RESPONSES[404],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
    patch=extend_schema(
        operation_id="inventory_update",
        summary="Set the variant's inventory quantity",
        description=(
            "Requires MANAGER or superuser. quantity is required and replaces the "
            "current quantity. Its range is 0 to 2147483647."
        ),
        request=InventoryInputSerializer,
        responses={
            200: InventoryOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
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


@extend_schema(tags=["Manager Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    post=extend_schema(
        operation_id="images_create",
        summary="Upload a product image",
        description=(
            "Requires MANAGER or superuser. Submit the file field as multipart/form-data. "
            "Accepts one static JPEG, PNG, or WebP up to 5 MiB, with up to 6000 pixels "
            "per side and up to 20 million pixels total. "
            "A storage failure returns 503 image_storage_unavailable."
        ),
        request={"multipart/form-data": ImageInputSerializer},
        responses={
            201: ProductImageSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            503: ERROR_RESPONSES[503],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
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


@extend_schema(tags=["Manager Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    patch=extend_schema(
        operation_id="images_update",
        summary="Update metadata or replace an image",
        description=(
            "Requires MANAGER or superuser. PATCH accepts partial updates and file "
            "is optional. Submit metadata as JSON or form-urlencoded data and use "
            "multipart/form-data to replace the file. Upload limits match those for creation."
        ),
        request={
            "multipart/form-data": ImageInputSerializer,
            "application/json": IMAGE_METADATA_SCHEMA,
            "application/x-www-form-urlencoded": IMAGE_METADATA_SCHEMA,
        },
        responses={
            200: ProductImageSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            503: ERROR_RESPONSES[503],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
    delete=extend_schema(
        operation_id="images_delete",
        summary="Delete an image",
        request=None,
        responses={
            204: None,
            404: ERROR_RESPONSES[404],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
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
