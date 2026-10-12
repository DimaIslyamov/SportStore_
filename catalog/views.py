from django.http import Http404
from rest_framework import generics
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny, IsAuthenticated, SAFE_METHODS

from accounts.permissions import IsManager

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
