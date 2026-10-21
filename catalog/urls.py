from django.urls import path

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
]
