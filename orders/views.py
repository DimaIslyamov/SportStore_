from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsCustomer

from . import services
from .serializers import CheckoutInputSerializer, CheckoutOrderOutputSerializer


class CheckoutView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer]
    parser_classes = [JSONParser]
    serializer_class = CheckoutInputSerializer
    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = services.checkout(
            user=request.user,
            address_id=serializer.validated_data["address_id"],
        )

        return Response(
            CheckoutOrderOutputSerializer(order).data,
            status=status.HTTP_201_CREATED,
        )
