import json
import logging
from uuid import uuid4

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404, JsonResponse
from django.http.multipartparser import MultiPartParserError

from .errors import error_payload
from .logging import logger, request_id_context


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.request_id = str(uuid4())
        token = request_id_context.set(request.request_id)

        try:
            response = self.get_response(request)
            if (
                request.path_info.startswith("/api/")
                and response.status_code >= 400
                and not response.streaming
            ):

                try:
                    data = (
                        json.loads(response.content) if not response.streaming else {}
                    )
                except (ValueError, UnicodeDecodeError):
                    data = {}

                payload = error_payload(data, response.status_code, request.request_id)
                content = JsonResponse(payload).content
                response.content = content

                if hasattr(response, "data"):
                    response.data = payload

                response["Content-Type"] = "application/json"
                response["Content-Length"] = str(len(content))

                logger.log(
                    logging.ERROR if response.status_code >= 500 else logging.WARNING,
                    "api_error",
                    extra={
                        "request_id": request.request_id,
                        "status_code": response.status_code,
                        "error_code": payload["error"]["code"],
                        "exception_type": getattr(request, "exception_type", None),
                    },
                )

            else:
                logger.info(
                    "request_completed",
                    extra={
                        "request_id": request.request_id,
                        "status_code": response.status_code,
                    },
                )

            response["X-Request-ID"] = request.request_id

            return response

        finally:
            request_id_context.reset(token)

    def process_exception(self, request, exception):
        if not request.path_info.startswith("/api/") or isinstance(
            exception,
            (
                Http404,
                PermissionDenied,
                BadRequest,
                SuspiciousOperation,
                MultiPartParserError,
            ),
        ):
            return None
        request.exception_type = type(exception).__name__

        return JsonResponse(error_payload({}, 500, request.request_id), status=500)
    