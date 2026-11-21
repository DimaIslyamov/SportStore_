from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse
from rest_framework import serializers


class ApiErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
    details = serializers.DictField(child=serializers.JSONField(), read_only=True)
    request_id = serializers.UUIDField(read_only=True)


class ApiErrorSerializer(serializers.Serializer):
    error = ApiErrorDetailSerializer(read_only=True)


class TokenPairOutputSerializer(serializers.Serializer):
    access = serializers.CharField(read_only=True)
    refresh = serializers.CharField(read_only=True)


class HealthOutputSerializer(serializers.Serializer):
    status = serializers.CharField(read_only=True)
    database = serializers.CharField(read_only=True)


REQUEST_ID_RESPONSE = OpenApiParameter(
    name="X-Request-ID",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.HEADER,
    response=True,
    description=(
        "Server-assigned UUID for this HTTP request. It matches error.request_id "
        "in the error body. Each replay receives a new request ID."
    ),
)

RETRY_AFTER_RESPONSE = OpenApiParameter(
    name="Retry-After",
    type=OpenApiTypes.INT,
    location=OpenApiParameter.HEADER,
    response=[429],
    description="Number of seconds to wait before retrying the request.",
)

IDEMPOTENCY_KEY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        "UUID in the hyphenated 8-4-4-4-12 format. To retry the same operation, "
        "reuse the original key and keep the fields that affect the operation unchanged. "
        "Supply this header independently of authentication and request tracing."
    ),
)

ERROR_RESPONSES = {
    400: OpenApiResponse(
        response=ApiErrorSerializer,
        description=(
            "Invalid input may return VALIDATION_ERROR with field details, "
            "parse_error, or an operation-specific error code."
        ),
    ),
    401: OpenApiResponse(
        response=ApiErrorSerializer,
        description=(
            "The JWT is missing, invalid, or expired, or the submitted credentials "
            "or token were rejected. Codes include not_authenticated, token_not_valid, "
            "and no_active_account."
        ),
    ),
    403: OpenApiResponse(
        response=ApiErrorSerializer,
        description="permission_denied / PERMISSION_DENIED: insufficient permissions.",
    ),
    404: OpenApiResponse(
        response=ApiErrorSerializer,
        description=(
            "The resource is missing or outside the caller's visibility. "
            "Returns not_found or an operation-specific error code."
        ),
    ),
    409: OpenApiResponse(
        response=ApiErrorSerializer,
        description="State or key reuse conflict. See the operation for specific codes.",
    ),
    415: OpenApiResponse(
        response=ApiErrorSerializer,
        description="unsupported_media_type: the Content-Type is unsupported.",
    ),
    429: OpenApiResponse(
        response=ApiErrorSerializer,
        description="throttled: request limit exceeded. Retry after the indicated delay.",
    ),
    503: OpenApiResponse(
        response=ApiErrorSerializer,
        description="Temporary unavailability. See the operation for specific causes.",
    ),
}

COMMON_ERRORS = {
    405: OpenApiResponse(
        response=ApiErrorSerializer,
        description="method_not_allowed. The Allow header lists supported methods.",
    ),
    406: OpenApiResponse(
        response=ApiErrorSerializer,
        description="not_acceptable: the requested response media type is unavailable.",
    ),
    500: OpenApiResponse(
        response=ApiErrorSerializer,
        description="INTERNAL_ERROR: a generic server error with safe response details.",
    ),
}

AUTH_ERRORS = {
    401: ERROR_RESPONSES[401],
    403: ERROR_RESPONSES[403],
}


def require_command_fields(result, generator, request, public):
    required_fields = {
        "cart_items_update": "quantity",
        "inventory_update": "quantity",
        "orders_status_update": "status",
    }
    schemas = result["components"]["schemas"]

    for path_item in result["paths"].values():
        operation = path_item.get("patch", {})
        field = required_fields.get(operation.get("operationId"))
        if field is None:
            continue

        body = operation["requestBody"]
        body["required"] = True
        for media_type in body["content"].values():
            schema = media_type["schema"]
            if "$ref" in schema:
                component_name = schema["$ref"].rsplit("/", 1)[-1]
                schema = schemas[component_name]
            schema["required"] = [field]

    return result
