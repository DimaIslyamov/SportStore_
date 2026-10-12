from django.urls import path

from rest_framework.routers import SimpleRouter

from .views import (
    AccountTokenRefreshView,
    AddressViewSet,
    EmailTokenObtainPairView,
    LogoutView,
    MeView,
    RegisterView,
)


app_name = "accounts"
router = SimpleRouter()
router.register("addresses", AddressViewSet, basename="address")

urlpatterns = [
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/token/", EmailTokenObtainPairView.as_view(), name="token"),
    path("auth/token/refresh/", AccountTokenRefreshView.as_view(), name="refresh"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("users/me/", MeView.as_view(), name="me"),
]

urlpatterns += router.urls
