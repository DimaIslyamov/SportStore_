from django.urls import path

from .views import CartItemCreateView, CartItemDetailView, CartView

app_name = "cart"

urlpatterns = [
    path("", CartView.as_view(), name="detail"),
    path("items/", CartItemCreateView.as_view(), name="item-create"),
    path("items/<int:pk>/", CartItemDetailView.as_view(), name="item-detail"),
]
