from rest_framework import generics, status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.permissions import IsCustomer
from orders.idempotency import parse_idempotency_key

from .serializers import PaymentInputSerializer, PaymentOutputSerializer
from .services import create_payment


class OrderPaymentView(generics.GenericAPIView):
    permission_classes = [IsAuthenticated, IsCustomer]
    parser_classes = [JSONParser]
    serializer_class = PaymentInputSerializer
    http_method_names = ["post", "options"]

    def post(self, request, pk):
        key = parse_idempotency_key(request.headers.get("Idempotency-Key"))
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payment, created = create_payment(
            user=request.user,
            order_id=pk,
            key=key,
            outcome=serializer.validated_data["outcome"],
        )
        return Response(
            PaymentOutputSerializer(payment).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
