from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError

from catalog.image_validation import prepare_image
from catalog.storage import OBJECT_PREFIX, S3Storage, StorageUnavailable


def test_sdk_upload_keys_type_and_delete(monkeypatch, settings, file_factory):
    settings.CATALOG_S3_BUCKET = "sportstore-catalog-example"
    settings.CATALOG_S3_REGION = "eu-north-1"
    puts, deletes, clients = [], [], []

    def put_object(**kwargs):
        assert kwargs["Body"].tell() == 0
        assert kwargs["Body"].read()
        puts.append(kwargs)

    sdk = SimpleNamespace(
        put_object=put_object, delete_object=lambda **kw: deletes.append(kw)
    )

    def client(service, **kwargs):
        clients.append((service, kwargs))
        return sdk

    monkeypatch.setattr(boto3, "client", client)
    storage = S3Storage()
    assert clients == []
    image = prepare_image(file_factory())
    first, second = storage.upload(image), storage.upload(image)
    assert first.key != second.key and first.key.startswith(OBJECT_PREFIX)
    assert first.key.endswith(".png") and "untrusted" not in first.key
    assert (
        first.url
        == f"https://sportstore-catalog-example.s3.eu-north-1.amazonaws.com/{first.key}"
    )
    assert puts[0]["ContentType"] == "image/png" and "ACL" not in puts[0]
    storage.delete(first.key)
    assert deletes == [{"Bucket": "sportstore-catalog-example", "Key": first.key}]
    with pytest.raises(StorageUnavailable):
        storage.delete("outside-prefix/object.png")
    assert len(deletes) == 1
    assert clients[0][0] == "s3"
    config = clients[0][1]["config"]
    assert config.connect_timeout == 3 and config.read_timeout == 10
    assert config.retries["total_max_attempts"] == 3


def test_sdk_error_is_safe(monkeypatch, settings, file_factory):
    settings.CATALOG_S3_BUCKET = "sportstore-catalog-example"
    settings.CATALOG_S3_REGION = "eu-north-1"

    def fail(**kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "DO-NOT-EXPOSE"}}, "PutObject"
        )

    monkeypatch.setattr(
        boto3, "client", lambda *a, **kw: SimpleNamespace(put_object=fail)
    )
    with pytest.raises(StorageUnavailable) as error:
        S3Storage().upload(prepare_image(file_factory()))
    assert "DO-NOT-EXPOSE" not in str(error.value.detail)


def test_missing_config_fails_only_when_uploading(settings, file_factory):
    settings.CATALOG_S3_BUCKET = ""
    settings.CATALOG_S3_REGION = ""
    storage = S3Storage()
    with pytest.raises(StorageUnavailable):
        storage.upload(prepare_image(file_factory()))


@pytest.mark.parametrize("operation", ["upload", "delete"])
@pytest.mark.parametrize("failure", ["client", "credentials", "timeout", "endpoint"])
def test_storage_errors_are_translated_without_leaking_details(
    monkeypatch, settings, file_factory, caplog, operation, failure
):
    from botocore.exceptions import (
        EndpointConnectionError,
        NoCredentialsError,
        ReadTimeoutError,
    )

    settings.CATALOG_S3_BUCKET = "sportstore-catalog-example"
    settings.CATALOG_S3_REGION = "eu-north-1"
    errors = {
        "client": ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "PRIVATE-ERROR-MARKER"}},
            "PutObject",
        ),
        "credentials": NoCredentialsError(),
        "timeout": ReadTimeoutError(
            endpoint_url="https://PRIVATE-ERROR-MARKER.invalid"
        ),
        "endpoint": EndpointConnectionError(
            endpoint_url="https://PRIVATE-ERROR-MARKER.invalid"
        ),
    }

    def fail(**kwargs):
        raise errors[failure]

    monkeypatch.setattr(
        boto3,
        "client",
        lambda *args, **kwargs: SimpleNamespace(put_object=fail, delete_object=fail),
    )
    with pytest.raises(StorageUnavailable) as error:
        if operation == "upload":
            S3Storage().upload(prepare_image(file_factory()))
        else:
            S3Storage().delete("sportstore/catalog/" + "a" * 32 + ".png")
    assert error.value.status_code == 503
    assert "PRIVATE-ERROR-MARKER" not in str(error.value.detail)
    assert "PRIVATE-ERROR-MARKER" not in caplog.text


@pytest.mark.parametrize(
    "key",
    [
        "",
        "outside-prefix/x.png",
        "sportstore/catalog/../x.png",
        "sportstore/catalog/" + "a" * 32 + ".svg",
        "sportstore/catalog/" + "a" * 31 + ".png",
    ],
)
def test_foreign_or_malformed_delete_keys_never_reach_sdk(key):
    # The autouse client guard fails if invalid keys reach the SDK.
    with pytest.raises(StorageUnavailable):
        S3Storage().delete(key)


@pytest.mark.parametrize(
    "bucket,region",
    [
        ("", "eu-north-1"),
        ("valid-example", ""),
        ("bucket/path", "eu-north-1"),
        ("valid-example", "https://evil.invalid"),
    ],
)
def test_invalid_configuration_is_rejected_before_sdk(
    settings, file_factory, bucket, region
):
    settings.CATALOG_S3_BUCKET = bucket
    settings.CATALOG_S3_REGION = region
    with pytest.raises(StorageUnavailable):
        S3Storage().upload(prepare_image(file_factory()))
