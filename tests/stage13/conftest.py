import pytest
from django.db import connection

from tests.stage12.conftest import (
    order_factory as order_factory,
    order_variants as order_variants,
    other_customer as other_customer,
    prepare_order_cart as prepare_order_cart,
)


@pytest.fixture(autouse=True)
def payments_use_postgresql(db):
    assert connection.vendor == "postgresql"
