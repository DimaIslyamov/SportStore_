from django.db.models import Q
from django.http import Http404
from rest_framework import generics
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated, SAFE_METHODS

from accounts.permissions import IsManager

from .filters import ProductQuerySerializer
from .pagination import ProductPagination
from .product_serializers import ProductDetailSerializer, ProductListSerializer
from .querysets import public_product_detail_queryset, public_product_queryset
from .models import Brand, Category
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


class CategoryListCreateView(CatalogAccessMixin, generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    queryset = Category.objects.filter(is_active=True)
    http_method_names = ["get", "post", "head", "options"]


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


class BrandListCreateView(CatalogAccessMixin, generics.ListCreateAPIView):
    serializer_class = BrandSerializer
    queryset = Brand.objects.filter(is_active=True)
    http_method_names = ["get", "post", "head", "options"]


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


class ProductListView(generics.ListAPIView):
    permission_classes = [AllowAny]
    http_method_names = ["get", "head", "options"]
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


class ProductDetailView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    http_method_names = ["get", "head", "options"]
    serializer_class = ProductDetailSerializer
    lookup_field = "slug"

    def get_queryset(self):
        return public_product_detail_queryset()
