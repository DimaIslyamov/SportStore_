from rest_framework import generics
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsCustomer

from .idempotency import idempotent_checkout, parse_idempotency_key
from .serializers import CheckoutInputSerializer


class CheckoutView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer]
    parser_classes = [JSONParser]
    serializer_class = CheckoutInputSerializer
    http_method_names = ["post", "options"]

    def post(self, request, *args, **kwargs):
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = idempotent_checkout(
            user=request.user,
            address_id=serializer.validated_data["address_id"],
            key=key,
        )

        return Response(result.body, status=result.status_code)
