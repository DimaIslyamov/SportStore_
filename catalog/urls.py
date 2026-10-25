from django.urls import path

from .manager_views import (
    ImageCreateView,
    ImageUpdateDeleteView,
    InventoryView,
    VariantCreateView,
    VariantUpdateView,
)
from .views import (
    BrandDetailView,
    BrandListCreateView,
    CategoryDetailView,
    CategoryListCreateView,
    ProductDetailView,
    ProductListView,
)


app_name = "catalog"

urlpatterns = [
    path("categories/", CategoryListCreateView.as_view(), name="category-list"),
    path(
        "categories/<slug:lookup>/",
        CategoryDetailView.as_view(),
        name="category-detail",
    ),
    path("brands/", BrandListCreateView.as_view(), name="brand-list"),
    path("brands/<slug:lookup>/", BrandDetailView.as_view(), name="brand-detail"),
    path("products/", ProductListView.as_view(), name="product-list"),
    path("products/<slug:slug>/", ProductDetailView.as_view(), name="product-detail"),
    path(
        "products/<int:product_id>/variants/",
        VariantCreateView.as_view(),
        name="variant-create",
    ),
    path(
        "products/<int:product_id>/variants/<int:variant_id>/",
        VariantUpdateView.as_view(),
        name="variant-update",
    ),
    path(
        "products/<int:product_id>/variants/<int:variant_id>/inventory/",
        InventoryView.as_view(),
        name="variant-inventory",
    ),
    path(
        "products/<int:product_id>/images/",
        ImageCreateView.as_view(),
        name="image-create",
    ),
    path(
        "products/<int:product_id>/images/<int:image_id>/",
        ImageUpdateDeleteView.as_view(),
        name="image-update-delete",
    ),
]
