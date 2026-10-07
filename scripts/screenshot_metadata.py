"""Read screenshot format and pixel dimensions from image headers offline."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
import zlib


JPEG_FRAMES = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})


def image_metadata(payload: bytes) -> dict:
    """Identify PNG/JPEG bytes; unavailable dimensions are null and unknown."""
    result = {"format": "UNKNOWN", "mime_type": "application/octet-stream",
              "width": None, "height": None, "dimension_status": "unknown"}
    width = height = 0
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        result.update(format="PNG", mime_type="image/png")
        if (len(payload) >= 33 and payload[8:16] == b"\x00\x00\x00\rIHDR" and
                zlib.crc32(payload[12:29]) == int.from_bytes(payload[29:33], "big")):
            width, height = struct.unpack(">II", payload[16:24])
    elif payload.startswith(b"\xff\xd8"):
        result.update(format="JPEG", mime_type="image/jpeg")
        position = 2
        while position < len(payload):
            if payload[position] != 0xFF:
                break
            while position < len(payload) and payload[position] == 0xFF:
                position += 1
            if position >= len(payload):
                break
            marker = payload[position]
            position += 1
            if marker in {0xD9, 0xDA, 0x00}:
                break
            if marker == 0x01 or 0xD0 <= marker <= 0xD7:
                continue
            if position + 2 > len(payload):
                break
            length = int.from_bytes(payload[position:position + 2], "big")
            if length < 2 or position + length > len(payload):
                break
            if marker in JPEG_FRAMES:
                if length >= 8:
                    components = payload[position + 7]
                    if components > 0 and length == 8 + components * 3:
                        height, width = struct.unpack(">HH", payload[position + 3:position + 7])
                break
            position += length
    if 0 < width < 2**31 and 0 < height < 2**31:
        result.update(width=width, height=height, dimension_status="known")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()
    records = []
    for path in args.images:
        payload = path.read_bytes()
        records.append({"file": path.as_posix(), "sha256": hashlib.sha256(payload).hexdigest(),
                        **image_metadata(payload)})
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
