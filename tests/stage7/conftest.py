from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import boto3
import pytest
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from rest_framework.test import APIClient

from catalog import services
from catalog.models import Brand, Category, Product, ProductImage, ProductVariant
from catalog.storage import OBJECT_PREFIX, StorageUnavailable, StoredObject


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch):
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-not-real")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-not-real")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test-not-real")

    def forbidden(*args, **kwargs):
        raise AssertionError("Real boto3 client is forbidden in Stage 7 tests.")

    monkeypatch.setattr(boto3, "client", forbidden)
    monkeypatch.setattr(boto3.session.Session, "client", forbidden)
    from botocore.client import BaseClient

    monkeypatch.setattr(BaseClient, "_make_api_call", forbidden)


class FakeStorage:
    def __init__(self):
        self.events = []
        self.fail_upload = False
        self.fail_delete = False

    def upload(self, image):
        if self.fail_upload:
            raise StorageUnavailable()
        key = f"{OBJECT_PREFIX}{uuid4().hex}.{image.extension}"
        self.events.append(("upload", key))
        return StoredObject(key, f"https://images.example.com/{key}")

    def delete(self, key):
        self.events.append(
            (
                "delete",
                key,
                connection.get_autocommit(),
                ProductImage.objects.filter(storage_key=key).exists(),
            )
        )
        if self.fail_delete:
            raise StorageUnavailable()


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(services, "get_storage", lambda: storage)
    return storage


@pytest.fixture
def stage7_product(db):
    category = Category.objects.create(name="Running", slug="stage7-running")
    brand = Brand.objects.create(name="SportLab", slug="stage7-sportlab")
    return Product.objects.create(
        name="Stage 7 Shirt", slug="stage7-shirt", category=category, brand=brand
    )


@pytest.fixture
def stage7_variant(stage7_product):
    return ProductVariant.objects.create(
        product=stage7_product, sku="STAGE7-M", price=Decimal("25.00")
    )


@pytest.fixture
def stage7_image(stage7_product):
    return ProductImage.objects.create(
        product=stage7_product,
        image_url="https://images.example.com/legacy.png",
        is_primary=True,
    )


@pytest.fixture
def manager_api(manager):
    client = APIClient()
    client.force_authenticate(user=manager)
    return client


@pytest.fixture
def file_factory():
    def create(format="PNG", size=(16, 16)):
        buffer = BytesIO()
        Image.new("RGB", size, color="red").save(buffer, format=format)
        return SimpleUploadedFile(
            "untrusted-name.bin",
            buffer.getvalue(),
            content_type="application/octet-stream",
        )

    return create


@pytest.fixture
def endpoint_urls(stage7_product, stage7_variant, stage7_image):
    base = f"/api/v1/products/{stage7_product.pk}"
    return SimpleNamespace(
        product=f"{base}/",
        variants=f"{base}/variants/",
        variant=f"{base}/variants/{stage7_variant.pk}/",
        inventory=f"{base}/variants/{stage7_variant.pk}/inventory/",
        images=f"{base}/images/",
        image=f"{base}/images/{stage7_image.pk}/",
    )
