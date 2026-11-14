import pytest
from django.db import connection

from core.logging import logger
from tests.stage12.conftest import (
    order_factory as order_factory,
    order_variants as order_variants,
    other_customer as other_customer,
    prepare_order_cart as prepare_order_cart,
)


@pytest.fixture
def events(caplog):
    logger.addHandler(caplog.handler)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)


@pytest.fixture(autouse=True)
def require_postgresql():
    assert connection.vendor == "postgresql"
