"""Validate real Pillow bytes; never trust the filename or supplied MIME type."""

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.exceptions import ValidationError

from catalog import image_validation
from catalog.image_validation import prepare_image


@pytest.mark.parametrize(
    "format,extension,mime",
    [
        ("JPEG", "jpg", "image/jpeg"),
        ("PNG", "png", "image/png"),
        ("WEBP", "webp", "image/webp"),
    ],
)
def test_verified_bytes_determine_type_and_rewind(
    file_factory, format, extension, mime
):
    file = file_factory(format=format)
    file.seek(7)
    prepared = prepare_image(file)
    assert prepared.extension == extension
    assert prepared.content_type == mime
    assert prepared.file is file and file.tell() == 0
    assert file.read()


@pytest.mark.parametrize(
    "payload", [b"", b"<svg></svg>", b"\xff\xd8\xffbroken", b"\x89PNG\r\n\x1a\n"]
)
def test_spoofed_and_truncated_files_are_rejected_and_rewound(payload):
    file = SimpleUploadedFile("photo.jpg", payload, content_type="image/jpeg")
    file.seek(len(payload))
    with pytest.raises(ValidationError):
        prepare_image(file)
    assert file.tell() == 0


@pytest.mark.parametrize("format", ["GIF", "BMP", "TIFF"])
def test_decodable_unsupported_formats_are_rejected(file_factory, format):
    file = file_factory(format=format)
    with pytest.raises(ValidationError, match="JPEG, PNG or WebP"):
        prepare_image(file)
    assert file.tell() == 0


@pytest.mark.parametrize("format", ["PNG", "WEBP"])
def test_animated_supported_formats_are_rejected(format):
    buffer = BytesIO()
    frames = [Image.new("RGB", (16, 16), color=c) for c in ("red", "blue")]
    frames[0].save(
        buffer,
        format=format,
        save_all=True,
        append_images=frames[1:],
        duration=100,
        loop=0,
    )
    file = SimpleUploadedFile("animated.bin", buffer.getvalue())
    with pytest.raises(ValidationError, match="Animated"):
        prepare_image(file)
    assert file.tell() == 0


@pytest.mark.parametrize("declared_size", [0, 5 * 1024 * 1024 + 1])
def test_oversize_bytes_rejected_even_if_declared_size_is_wrong(declared_size):
    file = SimpleUploadedFile("large.png", b"x" * (5 * 1024 * 1024 + 1))
    file.size = declared_size
    with pytest.raises(ValidationError, match="5 MiB"):
        prepare_image(file)


def test_exact_byte_limit_is_accepted(file_factory):
    payload = file_factory().read()
    file = SimpleUploadedFile(
        "padded.png", payload + b"\0" * (5 * 1024 * 1024 - len(payload))
    )
    assert prepare_image(file).content_type == "image/png"
    assert file.tell() == 0


@pytest.mark.parametrize("size", [(6001, 1), (1, 6001), (5000, 4001)])
def test_actual_dimension_and_pixel_limits(file_factory, size):
    file = file_factory(size=size)
    with pytest.raises(ValidationError, match="dimensions"):
        prepare_image(file)
    assert file.tell() == 0


@pytest.mark.parametrize("size", [(6000, 1), (1, 6000), (5000, 4000)])
def test_dimension_and_pixel_boundaries_are_inclusive(file_factory, size):
    file = file_factory(size=size)
    assert prepare_image(file).extension == "png"


@pytest.mark.parametrize("bomb_limit", [200, 100])
def test_pillow_decompression_bombs_are_errors(monkeypatch, file_factory, bomb_limit):
    file = file_factory(size=(16, 16))
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", bomb_limit)
    with pytest.raises(ValidationError, match="Invalid image"):
        prepare_image(file)
    assert file.tell() == 0


def test_invalid_input_is_rejected_before_load(monkeypatch, file_factory):
    # CRC validation must still run before the second open/load pass.
    file = file_factory()
    payload = bytearray(file.read())
    payload[-1] ^= 0xFF
    # Corrupt the IDAT CRC, since Pillow does not verify the IEND CRC.
    offset = payload.index(b"IDAT")
    length = int.from_bytes(payload[offset - 4 : offset], "big")
    payload[offset + 4 + length] ^= 0xFF
    file = SimpleUploadedFile("corrupt.png", bytes(payload))
    with pytest.raises(ValidationError):
        image_validation.prepare_image(file)
