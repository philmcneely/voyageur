"""
===============================================================================
Screenshot Check (is this base64 string a PNG we are willing to decode)
===============================================================================
A client's screenshot is untrusted bytes headed for an image decoder, so it is judged in layers, cheapest first, and the
expensive layers only run once the cheap ones pass. Nothing here draws or sends anything (overlay.py draws, llm.py sends).

    1  size       the base64 text is bounded before it is decoded (LimitExceeded, HTTP 413)
    2  base64     strict alphabet and padding; anything else is refused (no whitespace, no URL-safe variant, no data: prefix)
    3  magic      the eight PNG signature bytes, and a first chunk that is a 13-byte IHDR: the type is read from the bytes,
                  never from what the client says (`media_type` is only checked for equality with image/png)
    4  header     width and height are read from IHDR with struct, with no decoder involved: each at most 8192, the product at
                  most MAX_PIXELS, and both equal to the dimensions the client claimed
    5  integrity  only now does Pillow open the file: its own decompression-bomb limit is set to MAX_PIXELS and a
                  DecompressionBombWarning is an error, then verify() walks the chunks and checks their CRCs

Pixels are decoded later, by overlay.render, inside a killable worker under the request deadline.

Features:
    ✓ check_png(): the decoded bytes and their (width, height), or a ValueError naming the rule (never the content)
    ✓ MAX_BYTES (4 MB decoded), MAX_SIDE (8192), MAX_PIXELS (16 million: 4096 x 4096, a 2560 x 1440 viewport at dpr 2 fits)
    ✓ A refused screenshot says which rule it broke and nothing about the image

Environment Variables:
    None.

Author: PMAC
===============================================================================
"""
import base64
import binascii
import io
import struct
import threading
import warnings
from typing import Tuple

from PIL import Image

MAX_BYTES = 4 * 1024 * 1024
MAX_BASE64_CHARS = (MAX_BYTES + 2) // 3 * 4
MAX_SIDE = 8192
MAX_PIXELS = 16 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

Image.MAX_IMAGE_PIXELS = MAX_PIXELS          # Pillow warns above this and raises above twice this, before it decodes a pixel
_pillow = threading.Lock()                   # warnings.catch_warnings changes process-wide state: one thread at a time


def png_size(data: bytes) -> Tuple[int, int]:
    """(width, height) from the IHDR chunk of `data`; ValueError when the bytes do not start like a PNG."""
    if data[:8] != PNG_SIGNATURE:
        raise ValueError("the screenshot is not a PNG (its first bytes are not the PNG signature)")
    if len(data) < 33 or data[12:16] != b"IHDR" or struct.unpack(">I", data[8:12])[0] != 13:
        raise ValueError("the screenshot is not a PNG (no IHDR chunk follows the signature)")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def check_png(b64: str, width: int, height: int) -> bytes:
    """The PNG bytes of `b64`, after every check in the module header. ValueError when any rule is broken."""
    if len(b64) > MAX_BASE64_CHARS:
        raise ValueError(f"the screenshot is over {MAX_BYTES // (1024 * 1024)} MB")
    try:
        data = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("the screenshot is not valid base64") from None
    if not data or len(data) > MAX_BYTES:
        raise ValueError(f"the screenshot is empty or over {MAX_BYTES // (1024 * 1024)} MB")
    w, h = png_size(data)
    if not (1 <= w <= MAX_SIDE and 1 <= h <= MAX_SIDE):
        raise ValueError(f"the screenshot is over {MAX_SIDE} px on a side")
    if w * h > MAX_PIXELS:
        raise ValueError(f"the screenshot is over {MAX_PIXELS // 1_000_000} million pixels")
    if (w, h) != (width, height):
        raise ValueError("the screenshot's width and height do not match the PNG")
    with _pillow, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(data), formats=["PNG"]) as im:
                if im.size != (w, h):
                    raise ValueError("the screenshot's width and height do not match the PNG")
                im.verify()
        except ValueError:
            raise
        except Exception:                                   # a bomb warning, a CRC error, a truncated file, a bad chunk
            raise ValueError("the screenshot is not a readable PNG") from None
    return data


__all__ = ["MAX_BYTES", "MAX_BASE64_CHARS", "MAX_SIDE", "MAX_PIXELS", "check_png", "png_size"]
