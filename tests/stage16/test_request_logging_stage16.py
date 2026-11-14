import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID
from types import SimpleNamespace

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from core.logging import JsonFormatter, get_request_id, request_id_context
from core.middleware import RequestContextMiddleware


def test_success_new_uuid_ignores_client_and_matches_logs_stage16(
    api_client, events, monkeypatch
):
    monkeypatch.setattr("core.views.connection.cursor", lambda: DummyCursor())
    responses = [
        api_client.get("/api/v1/health/", HTTP_X_REQUEST_ID="FAKE-CLIENT-ID")
        for _ in range(2)
    ]
    assert responses[0].json() == {"status": "ok", "database": "ok"}
    ids = [r["X-Request-ID"] for r in responses]
    assert len(set(ids)) == 2
    for value in ids:
        assert str(UUID(value)) == value
    assert get_request_id() == "-"
    records = [r for r in events.records if r.name == "sportstore"]
    assert [r.request_id for r in records] == ids
    assert all(r.msg == "request_completed" and r.levelname == "INFO" for r in records)


class DummyCursor:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        pass

    def fetchone(self):
        return (1,)


def test_context_isolation_and_cleanup_stage16():
    barrier = Barrier(2)

    def view(request):
        before = get_request_id()
        barrier.wait(timeout=5)
        assert get_request_id() == before == request.request_id
        return HttpResponse("ok")

    middleware = RequestContextMiddleware(view)

    def call():
        response = middleware(RequestFactory().get("/api/check/"))
        assert get_request_id() == "-"
        return response["X-Request-ID"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: call(), range(2)))
    assert ids[0] != ids[1]


def test_finally_resets_context_when_next_layer_raises_stage16():
    def broken(request):
        raise RuntimeError("broken")

    token = request_id_context.set("outer-context")
    try:
        with pytest.raises(RuntimeError):
            RequestContextMiddleware(broken)(RequestFactory().get("/api/check/"))
        assert get_request_id() == "outer-context"
    finally:
        request_id_context.reset(token)


def test_json_formatter_uses_allowlist_not_messages_stage16():
    record = logging.LogRecord(
        "django.request",
        logging.ERROR,
        __file__,
        1,
        "SECRET password=%s",
        ("TOKEN",),
        None,
    )
    record.request = SimpleNamespace(
        request_id="server-request-id", body="PRIVATE-REQUEST"
    )
    record.body = "PRIVATE-BODY"
    record.authorization = "Bearer PRIVATE"
    record.exc_info = (RuntimeError, RuntimeError("PRIVATE-EXCEPTION"), None)
    payload = JsonFormatter().format(record)
    assert json.loads(payload)["event"] == "application_log"
    assert json.loads(payload)["request_id"] == "server-request-id"
    assert all(
        value not in payload for value in ("SECRET", "TOKEN", "PRIVATE", "Bearer")
    )


@pytest.mark.django_db
def test_real_error_does_not_log_credentials_or_personal_data_stage16(
    api_client, events
):
    secret = "Secret-Password-928!"
    email = "private-person@example.com"
    response = api_client.post(
        "/api/v1/auth/token/?phone=PRIVATE-PHONE",
        {"email": email, "password": secret},
        format="json",
        HTTP_AUTHORIZATION="Bearer PRIVATE-JWT",
        HTTP_COOKIE="sessionid=PRIVATE-COOKIE",
    )
    assert response.status_code == 401
    lines = "\n".join(JsonFormatter().format(r) for r in events.records)
    assert all(
        value not in lines
        for value in (secret, email, "PRIVATE-JWT", "PRIVATE-COOKIE", "PRIVATE-PHONE")
    )
