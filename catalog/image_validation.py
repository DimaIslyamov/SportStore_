from dataclasses import dataclass
from io import BytesIO
import warnings

from PIL import Image, UnidentifiedImageError
from rest_framework.exceptions import ValidationError

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_SIDE = 6000
MAX_IMAGE_PIXELS = 20_000_000
IMAGE_FORMATS = {
    "JPEG": ("jpg", "image/jpeg"),
    "PNG": ("png", "image/png"),
    "WEBP": ("webp", "image/webp"),
}


@dataclass(frozen=True)
class PreparedImage:
    file: object
    extension: str
    content_type: str


def prepare_image(file):
    if file.size > MAX_IMAGE_BYTES:
        raise ValidationError("Image must not exceed 5 MiB.")
    try:
        file.seek(0)
        payload = file.read(MAX_IMAGE_BYTES + 1)

        if len(payload) > MAX_IMAGE_BYTES:
            raise ValidationError("Image must not exceed 5 MiB.")

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)

            with Image.open(BytesIO(payload)) as image:
                if image.format not in IMAGE_FORMATS:
                    raise ValidationError("Use JPEG, PNG or WebP.")
                if getattr(image, "is_animated", False):
                    raise ValidationError("Animated images are not accepted.")

                width, height = image.size

                if (
                    max(width, height) > MAX_IMAGE_SIDE
                    or width * height > MAX_IMAGE_PIXELS
                ):
                    raise ValidationError("Image dimensions are too large.")

                extension, content_type = IMAGE_FORMATS[image.format]
                image.verify()

            with Image.open(BytesIO(payload)) as image:
                image.load()

    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise ValidationError("Invalid image content.") from exc
    finally:
        file.seek(0)

    return PreparedImage(file=file, extension=extension, content_type=content_type)
