from django.db.models import Q
from django.http import Http404
from rest_framework import generics, status
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated, SAFE_METHODS

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    extend_schema,
    extend_schema_view,
)

from accounts.permissions import IsManager
from core.schema import (
    AUTH_ERRORS,
    COMMON_ERRORS,
    ERROR_RESPONSES,
    REQUEST_ID_RESPONSE,
)

from . import services
from .filters import ProductQuerySerializer
from .manager_serializers import ProductInputSerializer, ProductOutputSerializer
from .pagination import ProductPagination
from .product_serializers import ProductDetailSerializer, ProductListSerializer
from .querysets import public_product_detail_queryset, public_product_queryset
from .models import Brand, Category, Product
from .serializers import BrandSerializer, CategorySerializer


class CatalogAccessMixin:
    def get_permissions(self):
        if self.request.method in SAFE_METHODS:
            return [AllowAny()]
        return [IsAuthenticated(), IsManager()]


class CatalogDetailLookupMixin:
    def get_object(self):
        lookup = self.kwargs["lookup"]
        if self.request.method == "PATCH":
            if not lookup.isdecimal():
                raise Http404
            filters = {"pk": lookup}
        else:
            filters = {"slug": lookup}

        obj = get_object_or_404(self.get_queryset(), **filters)
        self.check_object_permissions(self.request, obj)
        return obj


@extend_schema(tags=["Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="categories_list",
        summary="Получить активные категории без пагинации",
        responses={
            200: CategorySerializer(many=True),
            401: ERROR_RESPONSES[401],
            **COMMON_ERRORS,
        },
    ),
    post=extend_schema(
        operation_id="categories_create",
        summary="Создать категорию",
        description="Требуется MANAGER или superuser. parent — ID категории или null.",
        request=CategorySerializer,
        responses={
            201: CategorySerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class CategoryListCreateView(CatalogAccessMixin, generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True)
    http_method_names = ["get", "post", "head", "options"]


@extend_schema(tags=["Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="categories_retrieve",
        summary="Получить активную категорию по slug",
        parameters=[
            OpenApiParameter(
                "lookup",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                required=True,
                description="Slug категории, например running.",
            ),
        ],
        responses={
            200: CategorySerializer,
            401: ERROR_RESPONSES[401],
            404: ERROR_RESPONSES[404],
            **COMMON_ERRORS,
        },
    ),
    patch=extend_schema(
        operation_id="categories_update",
        summary="Частично изменить категорию по ID",
        description="MANAGER или superuser. Slug нельзя изменить после создания.",
        parameters=[
            OpenApiParameter(
                "lookup",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                required=True,
                pattern="^[0-9]+$",
                description="Числовой ID в строковом сегменте, например 12.",
            ),
        ],
        request=CategorySerializer,
        responses={
            200: CategorySerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class CategoryDetailView(
    CatalogAccessMixin,
    CatalogDetailLookupMixin,
    generics.RetrieveUpdateAPIView,
):
    serializer_class = CategorySerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = Category.objects.all()
        if self.request.method != "PATCH":
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema(tags=["Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="brands_list",
        summary="Получить активные бренды без пагинации",
        responses={
            200: BrandSerializer(many=True),
            401: ERROR_RESPONSES[401],
            **COMMON_ERRORS,
        },
    ),
    post=extend_schema(
        operation_id="brands_create",
        summary="Создать бренд",
        description="Требуется MANAGER или superuser.",
        request=BrandSerializer,
        responses={
            201: BrandSerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class BrandListCreateView(CatalogAccessMixin, generics.ListCreateAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.filter(is_active=True)
    http_method_names = ["get", "post", "head", "options"]


@extend_schema(tags=["Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="brands_retrieve",
        summary="Получить активный бренд по slug",
        parameters=[
            OpenApiParameter(
                "lookup",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                required=True,
                description="Slug бренда, например nike.",
            ),
        ],
        responses={
            200: BrandSerializer,
            401: ERROR_RESPONSES[401],
            404: ERROR_RESPONSES[404],
            **COMMON_ERRORS,
        },
    ),
    patch=extend_schema(
        operation_id="brands_update",
        summary="Частично изменить бренд по ID",
        description="MANAGER или superuser. Slug после создания неизменяем.",
        parameters=[
            OpenApiParameter(
                "lookup",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                required=True,
                pattern="^[0-9]+$",
                description="Числовой ID бренда в строковом сегменте.",
            ),
        ],
        request=BrandSerializer,
        responses={
            200: BrandSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class BrandDetailView(
    CatalogAccessMixin,
    CatalogDetailLookupMixin,
    generics.RetrieveUpdateAPIView,
):
    serializer_class = BrandSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = Brand.objects.all()
        if self.request.method != "PATCH":
            queryset = queryset.filter(is_active=True)
        return queryset


@extend_schema(tags=["Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="products_list",
        summary="Каталог с фильтрами, поиском и пагинацией",
        description=(
            "Публичные активные товары с активными Category/Brand и вариантом. "
            "category/brand — slug. Цена — минимальная цена активного варианта. "
            "search ищет по name и description. ordering: name, price, -price; "
            "по умолчанию name. min_price <= max_price. Страница — 12 товаров; "
            "page_size и page=last не поддерживаются."
        ),
        parameters=[ProductQuerySerializer],
        responses={
            200: ProductListSerializer(many=True),
            400: ERROR_RESPONSES[400],
            401: ERROR_RESPONSES[401],
            404: ERROR_RESPONSES[404],
            **COMMON_ERRORS,
        },
    ),
    post=extend_schema(
        operation_id="products_create",
        summary="Создать Product",
        description=(
            "MANAGER или superuser. category и brand — числовые ID. "
            "У Product нет price: цены задаются у вариантов отдельным endpoint."
        ),
        request=ProductInputSerializer,
        responses={
            201: ProductOutputSerializer,
            400: ERROR_RESPONSES[400],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class ProductListView(CatalogAccessMixin, generics.ListAPIView):
    http_method_names = ["get", "post", "head", "options"]
    serializer_class = ProductListSerializer
    pagination_class = ProductPagination

    def get_queryset(self):
        params = ProductQuerySerializer(data=self.request.query_params.dict())
        params.is_valid(raise_exception=True)
        data = params.validated_data
        queryset = public_product_queryset()

        if "category" in data:
            queryset = queryset.filter(category__slug=data["category"])
        if "brand" in data:
            queryset = queryset.filter(brand__slug=data["brand"])
        if "min_price" in data:
            queryset = queryset.filter(from_price__gte=data["min_price"])
        if "max_price" in data:
            queryset = queryset.filter(from_price__lte=data["max_price"])
        if "in_stock" in data:
            queryset = queryset.filter(in_stock=data["in_stock"])
        if data.get("search"):
            queryset = queryset.filter(
                Q(name__icontains=data["search"])
                | Q(description__icontains=data["search"])
            )

        ordering = {
            "name": ("name", "id"),
            "price": ("from_price", "id"),
            "-price": ("-from_price", "id"),
        }
        return queryset.order_by(*ordering[data["ordering"]])

    def post(self, request, *args, **kwargs):
        data = request.data.dict() if hasattr(request.data, "dict") else request.data
        serializer = ProductInputSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        product = services.create_product(**serializer.validated_data)
        return Response(
            ProductOutputSerializer(product).data, status=status.HTTP_201_CREATED
        )


@extend_schema(tags=["Catalog"], parameters=[REQUEST_ID_RESPONSE])
@extend_schema_view(
    get=extend_schema(
        operation_id="products_retrieve",
        summary="Получить Product detail по slug",
        parameters=[
            OpenApiParameter(
                "slug",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                required=True,
                description="Slug товара, например running-shirt.",
            ),
        ],
        responses={
            200: ProductDetailSerializer,
            401: ERROR_RESPONSES[401],
            404: ERROR_RESPONSES[404],
            **COMMON_ERRORS,
        },
    ),
    patch=extend_schema(
        operation_id="products_update",
        summary="Частично изменить Product по ID",
        description=(
            "MANAGER или superuser. Параметр называется slug по существующему "
            "URLconf, но для PATCH принимает числовой ID. Slug неизменяем. "
            "Ответ manager имеет category/brand IDs, а не public nested objects."
        ),
        parameters=[
            OpenApiParameter(
                "slug",
                OpenApiTypes.STR,
                OpenApiParameter.PATH,
                required=True,
                pattern="^[0-9]+$",
                description="ID товара в существующем строковом сегменте пути.",
            ),
        ],
        request=ProductInputSerializer,
        responses={
            200: ProductOutputSerializer,
            400: ERROR_RESPONSES[400],
            404: ERROR_RESPONSES[404],
            415: ERROR_RESPONSES[415],
            **AUTH_ERRORS,
            **COMMON_ERRORS,
        },
    ),
)
class ProductDetailView(CatalogAccessMixin, generics.RetrieveAPIView):
    http_method_names = ["get", "patch", "head", "options"]
    serializer_class = ProductDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return public_product_detail_queryset()

    def patch(self, request, *args, **kwargs):
        lookup = kwargs["slug"]
        if not lookup.isascii() or not lookup.isdecimal():
            raise Http404
        product = get_object_or_404(Product, pk=lookup)
        data = request.data.dict() if hasattr(request.data, "dict") else request.data
        serializer = ProductInputSerializer(product, data=data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = services.update_product(product.pk, **serializer.validated_data)
        return Response(ProductOutputSerializer(updated).data)
