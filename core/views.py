from django.db import DatabaseError, connection
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from drf_spectacular.utils import (
    OpenApiExample,
    extend_schema,
)

from .schema import (
    COMMON_ERRORS,
    ERROR_RESPONSES,
    REQUEST_ID_RESPONSE,
    HealthOutputSerializer,
)


@extend_schema(
    operation_id="health_retrieve",
    tags=["Health"],
    summary="Проверить API и соединение с PostgreSQL",
    auth=[],
    parameters=[REQUEST_ID_RESPONSE],
    responses={
        200: HealthOutputSerializer,
        503: ERROR_RESPONSES[503],
        **COMMON_ERRORS,
    },
    examples=[
        OpenApiExample(
            "База доступна",
            value={"status": "ok", "database": "ok"},
            response_only=True,
            status_codes=[200],
        ),
        OpenApiExample(
            "База недоступна",
            value={
                "error": {
                    "code": "SERVICE_UNAVAILABLE",
                    "message": "Service unavailable.",
                    "details": {"status": "error", "database": "unavailable"},
                    "request_id": "8f413c8d-2975-4ed3-951f-4a8c875491e2",
                }
            },
            response_only=True,
            status_codes=[503],
        ),
    ],
)
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return Response(
            {"status": "error", "database": "unavailable"},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return Response(
        {"status": "ok", "database": "ok"},
        status=status.HTTP_200_OK,
    )
