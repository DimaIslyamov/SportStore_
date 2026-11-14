from uuid import UUID


def error_content(response):
    body = response.json()
    assert set(body) == {"error"}
    error = body["error"]
    assert set(error) == {"code", "message", "details", "request_id"}
    assert error["request_id"] == response["X-Request-ID"]
    assert str(UUID(error["request_id"])) == error["request_id"]
    return {key: value for key, value in error.items() if key != "request_id"}


def stored_domain_error(response):
    error = error_content(response)
    assert error["details"] == {}
    return {"code": error["code"], "detail": error["message"]}
