"""Additional inventory failures must preserve existing stock and timestamps."""

import pytest
from django.db import IntegrityError

from catalog import services
from catalog.models import Inventory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("value", [-1, "abc", "1.5", None, True, [], {}, 2147483648])
def test_invalid_stock_keeps_existing_row(
    manager_api, endpoint_urls, stage7_variant, value
):
    inventory = Inventory.objects.create(variant=stage7_variant, quantity=11)
    before = (inventory.quantity, inventory.updated_at)
    response = manager_api.patch(
        endpoint_urls.inventory, {"quantity": value}, format="json"
    )
    assert (
        response.status_code == 400
        and "quantity" in response.json()["error"]["details"]
    )
    inventory.refresh_from_db()
    assert (inventory.quantity, inventory.updated_at) == before


@pytest.mark.parametrize("existing", [False, True])
def test_sql_failure_rolls_back_inventory(
    stage7_product, stage7_variant, monkeypatch, existing
):
    if existing:
        inventory = Inventory.objects.create(variant=stage7_variant, quantity=11)
        before = inventory.updated_at
    original = Inventory.save

    def save_then_fail(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if self.quantity == 9:
            raise IntegrityError("Injected stock write failure")

    monkeypatch.setattr(Inventory, "save", save_then_fail)
    with pytest.raises(IntegrityError):
        services.update_inventory(stage7_product.pk, stage7_variant.pk, quantity=9)
    if existing:
        inventory.refresh_from_db()
        assert inventory.quantity == 11 and inventory.updated_at == before
    else:
        assert not Inventory.objects.filter(variant=stage7_variant).exists()


@pytest.mark.parametrize("method", ["get", "patch"])
def test_missing_variant_is_404_without_stock_write(
    manager_api, stage7_product, method
):
    url = f"/api/v1/products/{stage7_product.pk}/variants/99999999/inventory/"
    response = getattr(manager_api, method)(url, {"quantity": 9}, format="json")
    assert response.status_code == 404
    assert Inventory.objects.count() == 0


def test_maximum_quantity_round_trips(manager_api, endpoint_urls, stage7_variant):
    response = manager_api.patch(
        endpoint_urls.inventory, {"quantity": 2147483647}, format="json"
    )
    assert response.status_code == 200
    assert Inventory.objects.get(variant=stage7_variant).quantity == 2147483647
