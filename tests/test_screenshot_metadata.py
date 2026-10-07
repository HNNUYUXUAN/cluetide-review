import importlib.util
from pathlib import Path
import struct
import zlib

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("screenshot_metadata", ROOT / "scripts/screenshot_metadata.py")
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


def png_header(width, height):
    chunk = b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + chunk + struct.pack(">I", zlib.crc32(chunk))


def jpeg_header(width, height, marker=0xC0):
    # JFIF density bytes are unrelated to pixel dimensions.
    jfif = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    frame = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    return b"\xff\xd8\xff\xe0" + struct.pack(">H", len(jfif) + 2) + jfif + b"\xff" + bytes([marker]) + struct.pack(">H", len(frame) + 2) + frame


def test_png_dimensions_require_valid_ihdr():
    assert metadata.image_metadata(png_header(640, 480)) == {
        "format": "PNG", "mime_type": "image/png", "width": 640, "height": 480,
        "dimension_status": "known"}


@pytest.mark.parametrize("marker", [0xC0, 0xC2])
def test_jpeg_dimensions_use_frame_after_jfif(marker):
    assert metadata.image_metadata(jpeg_header(1265, 712, marker)) == {
        "format": "JPEG", "mime_type": "image/jpeg", "width": 1265, "height": 712,
        "dimension_status": "known"}


@pytest.mark.parametrize("payload", [b"", b"not an image", png_header(0, 10),
    png_header(10, 10)[:-1], png_header(10, 10)[:-4] + b"\x00" * 4,
    jpeg_header(0, 10), jpeg_header(10, 10)[:-1], b"\xff\xd8\xff\xe0\x00\x01",
    b"\xff\xd8\xff\xe0\xff\xff", b"\xff\xd8\xff\xda\x00\x02"])
def test_unavailable_dimensions_are_unknown(payload):
    result = metadata.image_metadata(payload)
    assert result["width"] is None and result["height"] is None
    assert result["dimension_status"] == "unknown"
