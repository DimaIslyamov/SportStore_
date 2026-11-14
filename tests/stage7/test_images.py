import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from rest_framework.test import APIClient

from catalog import services
from catalog.image_validation import MAX_IMAGE_BYTES, prepare_image
from catalog.models import Product, ProductImage
from catalog.storage import OBJECT_PREFIX

pytestmark = pytest.mark.django_db(transaction=True)


def test_upload_metadata_replace_delete_and_public_output(
    manager_api, endpoint_urls, stage7_variant, stage7_image, file_factory, fake_storage
):
    response = manager_api.post(
        endpoint_urls.images,
        {
            "file": file_factory(),
            "alt_text": "Front",
            "is_primary": "true",
            "position": 3,
        },
        format="multipart",
    )
    assert response.status_code == 201
    image = ProductImage.objects.get(pk=response.json()["id"])
    old_key = image.storage_key
    assert (
        old_key.startswith(OBJECT_PREFIX)
        and image.image_url == response.json()["image_url"]
    )
    assert "storage_key" not in response.json()
    url = f"{endpoint_urls.images}{image.pk}/"
    assert (
        manager_api.patch(url, {"alt_text": "Updated"}, format="multipart").status_code
        == 200
    )
    image.refresh_from_db()
    assert image.is_primary is True and image.storage_key == old_key
    assert len(fake_storage.events) == 1
    response = manager_api.patch(url, {"file": file_factory()}, format="multipart")
    assert response.status_code == 200
    image.refresh_from_db()
    assert (
        image.storage_key != old_key
        and image.alt_text == "Updated"
        and image.is_primary
    )
    assert fake_storage.events[-1] == ("delete", old_key, True, False)
    public = APIClient().get(f"/api/v1/products/{image.product.slug}/").json()
    assert public["primary_image"]["image_url"] == image.image_url
    new_key = image.storage_key
    response = manager_api.delete(url)
    assert response.status_code == 204 and response.content == b""
    assert not ProductImage.objects.filter(pk=image.pk).exists()
    assert fake_storage.events[-1] == ("delete", new_key, True, False)
    public = APIClient().get(f"/api/v1/products/{image.product.slug}/").json()
    assert public["primary_image"]["id"] == stage7_image.pk


def test_primary_switch_clear_and_legacy_delete_do_not_call_s3(
    manager_api, stage7_image, endpoint_urls, fake_storage
):
    other = ProductImage.objects.create(
        product=stage7_image.product, image_url="https://images.example.com/other.png"
    )
    url = f"{endpoint_urls.images}{other.pk}/"
    assert (
        manager_api.patch(url, {"is_primary": True}, format="json").status_code == 200
    )
    stage7_image.refresh_from_db()
    assert stage7_image.is_primary is False
    assert ProductImage.objects.get(pk=other.pk).is_primary is True
    assert (
        manager_api.patch(url, {"is_primary": False}, format="json").status_code == 200
    )
    assert not ProductImage.objects.filter(
        product=stage7_image.product, is_primary=True
    ).exists()
    assert manager_api.delete(url).status_code == 204
    assert fake_storage.events == []


def test_replace_legacy_url_never_deletes_guessed_old_object(
    manager_api, endpoint_urls, stage7_image, file_factory, fake_storage
):
    assert stage7_image.storage_key == ""
    assert (
        manager_api.patch(
            endpoint_urls.image, {"file": file_factory()}, format="multipart"
        ).status_code
        == 200
    )
    stage7_image.refresh_from_db()
    assert stage7_image.storage_key
    assert [event[0] for event in fake_storage.events] == ["upload"]


@pytest.mark.parametrize(
    "payload",
    [
        {"image_url": "https://evil.example/x"},
        {"storage_key": "evil"},
        {"product": 1},
        {"position": -1},
        {"file": "https://evil.example/x"},
    ],
)
def test_image_rejects_invalid_fields(
    manager_api, endpoint_urls, payload, fake_storage
):
    assert (
        manager_api.patch(endpoint_urls.image, payload, format="json").status_code
        == 400
    )
    assert fake_storage.events == []


@pytest.mark.parametrize(
    "kind",
    ["missing", "html", "gif", "too-large", "dimensions", "pixels", "corrupt-png"],
)
def test_invalid_file_never_reaches_storage(
    manager_api, endpoint_urls, file_factory, fake_storage, monkeypatch, kind
):
    from catalog import image_validation

    data = {}
    if kind == "html":
        data["file"] = SimpleUploadedFile(
            "fake.png", b"<svg></svg>", content_type="image/png"
        )
    elif kind == "gif":
        data["file"] = file_factory(format="GIF")
    elif kind == "too-large":
        data["file"] = SimpleUploadedFile("big.png", b"x" * (MAX_IMAGE_BYTES + 1))
    elif kind == "dimensions":
        monkeypatch.setattr(image_validation, "MAX_IMAGE_SIDE", 8)
        data["file"] = file_factory(size=(9, 1))
    elif kind == "pixels":
        monkeypatch.setattr(image_validation, "MAX_IMAGE_PIXELS", 60)
        data["file"] = file_factory(size=(8, 8))
    elif kind == "corrupt-png":
        payload = bytearray(file_factory().read())
        offset = 8
        while offset < len(payload):
            length = int.from_bytes(payload[offset : offset + 4], "big")
            if payload[offset + 4 : offset + 8] == b"IDAT":
                payload[offset + 8 + length] ^= 0xFF
                break
            offset += length + 12
        else:
            raise AssertionError("Fixture PNG has no IDAT chunk")
        data["file"] = SimpleUploadedFile(
            "corrupt.png", bytes(payload), content_type="image/png"
        )
    response = manager_api.post(endpoint_urls.images, data, format="multipart")
    assert response.status_code == 400 and "file" in response.json()["error"]["details"]
    assert fake_storage.events == []


def test_wrong_image_parent_and_missing_product_do_not_upload(
    manager_api, stage7_product, stage7_image, file_factory, fake_storage
):
    other = Product.objects.create(
        name="Other",
        slug="other",
        category=stage7_product.category,
        brand=stage7_product.brand,
    )
    url = f"/api/v1/products/{other.pk}/images/{stage7_image.pk}/"
    assert (
        manager_api.patch(url, {"file": file_factory()}, format="multipart").status_code
        == 404
    )
    assert manager_api.delete(url).status_code == 404
    assert (
        manager_api.post(
            "/api/v1/products/99999999/images/",
            {"file": file_factory()},
            format="multipart",
        ).status_code
        == 404
    )
    assert fake_storage.events == []
    assert ProductImage.objects.filter(pk=stage7_image.pk).exists()


def test_upload_failure_does_not_create_or_replace(
    manager_api, endpoint_urls, stage7_image, file_factory, fake_storage
):
    fake_storage.fail_upload = True
    before = ProductImage.objects.count()
    for method, url in [("post", endpoint_urls.images), ("patch", endpoint_urls.image)]:
        response = getattr(manager_api, method)(
            url, {"file": file_factory()}, format="multipart"
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "image_storage_unavailable"
        assert (
            response.json()["error"]["message"]
            == "Image storage is temporarily unavailable."
        )
    stage7_image.refresh_from_db()
    assert ProductImage.objects.count() == before
    assert (
        stage7_image.image_url.endswith("legacy.png") and stage7_image.storage_key == ""
    )


@pytest.mark.parametrize("operation", ["create", "replace"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_sql_failure_cleans_only_new_upload_and_preserves_old(
    stage7_product,
    stage7_image,
    file_factory,
    fake_storage,
    monkeypatch,
    operation,
    cleanup_fails,
    caplog,
):
    fake_storage.fail_delete = cleanup_fails
    old_key = OBJECT_PREFIX + "a" * 32 + ".png"
    stage7_image.storage_key = old_key
    stage7_image.save(update_fields=["storage_key"])
    old_url = stage7_image.image_url

    def fail(*args, **kwargs):
        raise IntegrityError("Injected image SQL failure")

    if operation == "create":
        monkeypatch.setattr(ProductImage.objects, "create", fail)

        def call():
            return services.create_image(
                stage7_product.pk, file=prepare_image(file_factory()), is_primary=True
            )
    else:
        monkeypatch.setattr(ProductImage, "save", fail)

        def call():
            return services.update_image(
                stage7_product.pk,
                stage7_image.pk,
                file=prepare_image(file_factory()),
                alt_text="Changed",
            )

    with pytest.raises(IntegrityError):
        call()
    stage7_image.refresh_from_db()
    assert stage7_image.image_url == old_url and stage7_image.storage_key == old_key
    assert stage7_image.is_primary is True and stage7_image.alt_text == ""
    new_key = fake_storage.events[0][1]
    assert new_key != old_key
    assert fake_storage.events == [
        ("upload", new_key),
        ("delete", new_key, True, False),
    ]
    assert ProductImage.objects.count() == 1
    if cleanup_fails:
        assert "Catalog image cleanup failed" in caplog.text


def test_cleanup_failure_after_commit_does_not_undo_delete(
    manager_api, endpoint_urls, stage7_image, fake_storage, caplog
):
    key = OBJECT_PREFIX + "b" * 32 + ".png"
    stage7_image.storage_key = key
    stage7_image.save(update_fields=["storage_key"])
    fake_storage.fail_delete = True
    response = manager_api.delete(endpoint_urls.image)
    assert response.status_code == 204
    assert not ProductImage.objects.filter(pk=stage7_image.pk).exists()
    assert fake_storage.events == [("delete", key, True, False)]
    assert "Catalog image cleanup failed" in caplog.text


def test_explicit_primary_then_delete_restores_fallback_with_false_flag(
    manager_api, endpoint_urls, stage7_image, stage7_variant, fake_storage
):
    stage7_image.is_primary = False
    stage7_image.position = 5
    stage7_image.save()
    fallback = ProductImage.objects.create(
        product=stage7_image.product,
        image_url="https://images.example.com/fallback.png",
        position=0,
    )
    public = APIClient()
    public_url = f"/api/v1/products/{stage7_image.product.slug}/"
    assert public.get(public_url).json()["primary_image"]["id"] == fallback.pk
    response = manager_api.patch(
        endpoint_urls.image, {"is_primary": True}, format="json"
    )
    assert response.status_code == 200
    body = public.get(public_url).json()
    assert body["primary_image"]["id"] == stage7_image.pk
    assert body["primary_image"]["is_primary"] is True
    assert [row["id"] for row in body["images"]] == [fallback.pk, stage7_image.pk]
    assert manager_api.delete(endpoint_urls.image).status_code == 204
    body = public.get(public_url).json()
    assert body["primary_image"]["id"] == fallback.pk
    assert body["primary_image"]["is_primary"] is False
    assert fake_storage.events == []


@pytest.mark.parametrize("operation", ["create", "replace", "delete"])
def test_commit_failure_preserves_old_row_and_cleans_only_new_upload(
    operation, stage7_product, stage7_image, file_factory, fake_storage, monkeypatch
):
    from django.db import connection
    from catalog.image_validation import prepare_image

    key = OBJECT_PREFIX + "d" * 32 + ".png"
    stage7_image.storage_key = key
    stage7_image.save()

    def fail_commit():
        raise IntegrityError("Injected commit failure")

    monkeypatch.setattr(connection, "commit", fail_commit)
    with pytest.raises(IntegrityError):
        if operation == "create":
            services.create_image(
                stage7_product.pk, file=prepare_image(file_factory()), is_primary=True
            )
        elif operation == "replace":
            services.update_image(
                stage7_product.pk,
                stage7_image.pk,
                file=prepare_image(file_factory()),
                alt_text="Changed",
            )
        else:
            services.delete_image(stage7_product.pk, stage7_image.pk)
    stage7_image.refresh_from_db()
    assert stage7_image.storage_key == key and stage7_image.is_primary
    assert stage7_image.alt_text == "" and ProductImage.objects.count() == 1
    if operation == "delete":
        assert fake_storage.events == []
    else:
        new_key = fake_storage.events[0][1]
        assert fake_storage.events == [
            ("upload", new_key),
            ("delete", new_key, True, False),
        ]


def test_replacement_cleanup_failure_keeps_committed_new_image(
    manager_api, endpoint_urls, stage7_image, fake_storage, file_factory, caplog
):
    old_key = OBJECT_PREFIX + "c" * 32 + ".png"
    stage7_image.storage_key = old_key
    stage7_image.save()
    fake_storage.fail_delete = True
    response = manager_api.patch(
        endpoint_urls.image, {"file": file_factory()}, format="multipart"
    )
    assert response.status_code == 200
    stage7_image.refresh_from_db()
    assert stage7_image.storage_key != old_key
    assert fake_storage.events[-1] == ("delete", old_key, True, False)
    assert "Catalog image cleanup failed" in caplog.text


@pytest.mark.parametrize("operation", ["create", "replace", "delete"])
def test_image_services_refuse_outer_atomic_for_all_operations(
    operation, stage7_product, stage7_image, file_factory, fake_storage
):
    with transaction.atomic():
        with pytest.raises(RuntimeError, match="autocommit"):
            if operation == "create":
                services.create_image(
                    stage7_product.pk, file=prepare_image(file_factory())
                )
            elif operation == "replace":
                services.update_image(
                    stage7_product.pk,
                    stage7_image.pk,
                    file=prepare_image(file_factory()),
                )
            else:
                services.delete_image(stage7_product.pk, stage7_image.pk)
    assert ProductImage.objects.filter(pk=stage7_image.pk).exists()
    assert fake_storage.events == []
