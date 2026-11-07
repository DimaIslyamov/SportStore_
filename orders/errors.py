from rest_framework import status
from rest_framework.exceptions import APIException


class CheckoutError(APIException):
    default_code = "checkout-error"

    def __init__(self, code, detail, *, status_code=status.HTTP_409_CONFLICT):
        self.status_code = status_code
        super().__init__({"code": code, "detail": detail})
