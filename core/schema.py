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
        "UUID текущего HTTP-запроса, назначенный сервером. "
        "В error JSON совпадает с error.request_id; при replay создаётся заново."
    ),
)

IDEMPOTENCY_KEY_HEADER = OpenApiParameter(
    name="Idempotency-Key",
    type=OpenApiTypes.UUID,
    location=OpenApiParameter.HEADER,
    required=True,
    description=(
        "UUID в форме 8-4-4-4-12 с дефисами. Для повтора одной операции "
        "используй прежний ключ и тот же значимый payload. "
        "Это не JWT и не Request ID."
    ),
)

ERROR_RESPONSES = {
    400: OpenApiResponse(
        response=ApiErrorSerializer,
        description=(
            "Некорректный ввод: VALIDATION_ERROR с деталями полей, "
            "parse_error либо описанный у операции бизнес-код."
        ),
    ),
    401: OpenApiResponse(
        response=ApiErrorSerializer,
        description=(
            "Нет JWT, он невалиден/истёк либо отклонены credentials/token. "
            "Коды включают not_authenticated, token_not_valid, no_active_account."
        ),
    ),
    403: OpenApiResponse(
        response=ApiErrorSerializer,
        description="permission_denied / PERMISSION_DENIED: роль или доступ не разрешены.",
    ),
    404: OpenApiResponse(
        response=ApiErrorSerializer,
        description=(
            "Ресурс отсутствует или скрыт ownership/публичным queryset. "
            "Код not_found либо доменный код операции."
        ),
    ),
    409: OpenApiResponse(
        response=ApiErrorSerializer,
        description="Конфликт состояния или повторного ключа; см. коды операции.",
    ),
    415: OpenApiResponse(
        response=ApiErrorSerializer,
        description="unsupported_media_type: неподдерживаемый Content-Type.",
    ),
    503: OpenApiResponse(
        response=ApiErrorSerializer,
        description="Временная недоступность; конкретная причина указана у операции.",
    ),
}

COMMON_ERRORS = {
    405: OpenApiResponse(
        response=ApiErrorSerializer,
        description="method_not_allowed; разрешённые методы в заголовке Allow.",
    ),
    406: OpenApiResponse(
        response=ApiErrorSerializer,
        description="not_acceptable: API не может вернуть запрошенный media type.",
    ),
    500: OpenApiResponse(
        response=ApiErrorSerializer,
        description="INTERNAL_ERROR: безопасная ошибка без traceback и секретов.",
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
