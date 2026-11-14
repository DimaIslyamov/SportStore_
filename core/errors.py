from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework.exceptions import ValidationError
from rest_framework.views import exception_handler as drf_exception_handler

from .logging import get_request_id

HTTP_ERRORS = {
    400: ("BAD_REQUEST", "Bad request."),
    401: ("AUTHENTICATION_FAILED", "Authentication failed."),
    403: ("PERMISSION_DENIED", "Permission denied."),
    404: ("NOT_FOUND", "Not found."),
    405: ("METHOD_NOT_ALLOWED", "Method not allowed."),
    406: ("NOT_ACCEPTABLE", "Not acceptable."),
    409: ("CONFLICT", "Conflict."),
    415: ("UNSUPPORTED_MEDIA_TYPE", "Unsupported media type."),
    429: ("THROTTLED", "Request was throttled."),
    500: ("INTERNAL_ERROR", "Internal server error."),
    503: ("SERVICE_UNAVAILABLE", "Service unavailable."),
}


def error_payload(data, status_code, request_id, *, code=None, validation=False):
    fallback_code, fallback_message = HTTP_ERRORS.get(
        status_code, ("API_ERROR", "Request failed.")
    )

    if status_code == 500:
        code, message, details = fallback_code, fallback_message, {}

    elif (
        isinstance(data, dict)
        and set(data) == {"error"}
        and isinstance(data["error"], dict)
        and {"code", "message", "details", "request_id"} <= set(data["error"])
    ):
        previous = data["error"]
        code = previous["code"]
        message = previous["message"]
        details = previous["details"]

    elif validation:
        code, message = "VALIDATION_ERROR", "Invalid input."
        details = data if isinstance(data, dict) else {"non_field_errors": data}

    elif isinstance(data, dict) and isinstance(data.get("detail"), str):
        code = data.get("code", code or fallback_code)
        message = data["detail"]
        details = {
            key: value for key, value in data.items() if key not in {"code", "detail"}
        }

    else:
        code, message = code or fallback_code, fallback_message
        details = data if isinstance(data, dict) else {}

    return {
        "error": {
            "code": str(code),
            "message": str(message),
            "details": details,
            "request_id": request_id,
        }
    }


def exception_handler(exc, context):
    response = drf_exception_handler(exc, context)

    if response is None:
        return None

    codes = exc.get_codes() if hasattr(exc, "get_codes") else None

    if isinstance(exc, Http404):
        codes = "not_found"
    elif isinstance(exc, DjangoPermissionDenied):
        codes = "permission_denied"

    request = context.get("request")
    response.data = error_payload(
        response.data,
        response.status_code,
        getattr(request, "request_id", get_request_id()),
        code=codes if isinstance(codes, str) else None,
        validation=isinstance(exc, ValidationError),
    )

    return response
