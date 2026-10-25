from dataclasses import dataclass
import logging
import re
from urllib.parse import quote
from uuid import uuid4

from django.conf import settings
from rest_framework.exceptions import APIException

logger = logging.getLogger(__name__)
OBJECT_PREFIX = "sportstore/catalog/"


class StorageUnavailable(APIException):
    status_code = 503
    default_detail = "Image storage is temporarily unavailable."
    default_code = "image_storage_unavailable"


@dataclass(frozen=True)
class StoredObject:
    key: str
    url: str


class S3Storage:
    def _configuration(self):
        bucket = settings.CATALOG_S3_BUCKET
        region = settings.CATALOG_S3_REGION
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]", bucket):
            raise StorageUnavailable()
        if not re.fullmatch(r"[a-z]{2}-[a-z]+-\d+", region):
            raise StorageUnavailable()
        return bucket, region

    def _client(self, region):
        import boto3
        from botocore.config import Config

        return boto3.client(
            "s3",
            region_name=region,
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )

    def upload(self, image):
        from botocore.exceptions import BotoCoreError, ClientError

        bucket, region = self._configuration()
        key = f"{OBJECT_PREFIX}{uuid4().hex}.{image.extension}"
        try:
            image.file.seek(0)
            self._client(region).put_object(
                Bucket=bucket, Key=key, Body=image.file, ContentType=image.content_type
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("Catalog image upload failed; key=%s", key)
            raise StorageUnavailable() from exc
        return StoredObject(
            key=key,
            url=f"https://{bucket}.s3.{region}.amazonaws.com/{quote(key, safe='/')}",
        )

    def delete(self, key):
        from botocore.exceptions import BotoCoreError, ClientError

        if not re.fullmatch(
            re.escape(OBJECT_PREFIX) + r"[0-9a-f]{32}\.(jpg|png|webp)", key
        ):
            raise StorageUnavailable()
        bucket, region = self._configuration()
        try:
            self._client(region).delete_object(Bucket=bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise StorageUnavailable() from exc


def get_storage():
    return S3Storage()


def cleanup_object(storage, key):
    if not key:
        return
    try:
        storage.delete(key)
    except Exception:
        logger.error("Catalog image cleanup failed; key=%s", key)
