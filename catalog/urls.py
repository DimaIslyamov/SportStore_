from django.urls import path

from .views import (
    BrandDetailView,
    BrandListCreateView,
    CategoryDetailView,
    CategoryListCreateView,
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
]
