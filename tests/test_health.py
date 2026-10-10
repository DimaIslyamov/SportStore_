from unittest.mock import patch

import pytest
from django.db import OperationalError
from django.urls import reverse
from rest_framework.test import APIClient


@pytest.mark.django_db
def test_health_is_public_and_checks_database():
    client = APIClient()

    response = client.get(reverse("core:health"))

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_returns_503_without_exposing_database_error():
    client = APIClient()

    with patch(
        "core.views.connection.cursor",
        side_effect=OperationalError("internal database configuration"),
    ):
        response = client.get(reverse("core:health"))

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unavailable"}
    assert "internal database configuration" not in response.content.decode()


def test_health_rejects_post():
    client = APIClient()

    response = client.post(reverse("core:health"), {}, format="json")

    assert response.status_code == 405