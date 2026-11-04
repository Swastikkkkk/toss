"""Clipboard images: Windows hands over a bitmap and the phone wants a PNG.
The converter is plain Python, so it is tested here byte for byte; the
clipboard plumbing around it was tested on a real Windows machine."""
import struct
import zlib

import pytest

from toss.clipboard import dib_to_png, png_encode, png_size


def decode_png(png):
    """Just enough of a PNG reader for what png_encode writes (filter 0)."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, ihdr = 8, b"", None
    while pos < len(png):
        (length,) = struct.unpack(">I", png[pos:pos + 4])
        tag, data = png[pos + 4:pos + 8], png[pos + 8:pos + 8 + length]
        (crc,) = struct.unpack(">I", png[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + data) & 0xFFFFFFFF, f"bad CRC on {tag}"
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", data)
        elif tag == b"IDAT":
            idat += data
        pos += 12 + length
    w, h, depth, ctype = ihdr[:4]
    ch = 4 if ctype == 6 else 3
    raw = zlib.decompress(idat)
    rows = []
    for y in range(h):
        line = raw[y * (w * ch + 1):(y + 1) * (w * ch + 1)]
        assert line[0] == 0
        rows.append([tuple(line[1 + x * ch:1 + (x + 1) * ch]) for x in range(w)])
    return w, h, ch, rows


def make_dib(w, h, bpp, pixels_top_down, top_down=False, alpha=None, bitfields=False):
    """pixels_top_down: rows of (r, g, b). Builds what Windows would hand us."""
    stride = ((w * bpp + 31) // 32) * 4
    header = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, bpp,
                         3 if bitfields else 0, 0, 0, 0, 0, 0)
    if bitfields:
        header += struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    rows = pixels_top_down if top_down else list(reversed(pixels_top_down))
    body = b""
    for row in rows:
        line = b""
        for r, g, b in row:
            line += bytes([b, g, r]) + (bytes([alpha if alpha is not None else 0]) if bpp == 32 else b"")
        body += line + b"\x00" * (stride - len(line))
    return header + body


RED, GREEN, BLUE, WHITE = (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)
IMAGE = [[RED, GREEN, BLUE], [WHITE, RED, GREEN]]  # 3 wide, 2 tall, top row first


def test_png_encode_round_trips():
    pixels = bytes([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
    w, h, ch, rows = decode_png(png_encode(2, 2, pixels, 3))
    assert (w, h, ch) == (2, 2, 3)
    assert rows == [[(1, 2, 3), (4, 5, 6)], [(7, 8, 9), (10, 11, 12)]]


def test_png_size_reads_the_header():
    assert png_size(png_encode(7, 3, bytes(7 * 3 * 4), 4)) == (7, 3)
    assert png_size(b"not a png at all") is None


def test_32bit_bottom_up_bitmap_with_empty_alpha_becomes_opaque():
    """The common case, a screenshot: rows stored upside down, alpha left at 0."""
    w, h, ch, rows = decode_png(dib_to_png(make_dib(3, 2, 32, IMAGE)))
    assert (w, h, ch) == (3, 2, 4)
    assert rows[0] == [RED + (255,), GREEN + (255,), BLUE + (255,)], "flipped or channels swapped"
    assert rows[1] == [WHITE + (255,), RED + (255,), GREEN + (255,)]


def test_real_alpha_is_kept():
    _, _, _, rows = decode_png(dib_to_png(make_dib(3, 2, 32, IMAGE, alpha=128)))
    assert rows[0][0] == RED + (128,)


def test_top_down_bitmap_is_not_flipped():
    _, _, _, rows = decode_png(dib_to_png(make_dib(3, 2, 32, IMAGE, top_down=True)))
    assert rows[0][0] == RED + (255,) and rows[1][0] == WHITE + (255,)


def test_24bit_bitmap_with_row_padding():
    """3 pixels of 3 bytes is 9, padded to 12 per row: the padding must not leak in."""
    w, h, ch, rows = decode_png(dib_to_png(make_dib(3, 2, 24, IMAGE)))
    assert (w, h, ch) == (3, 2, 3)
    assert rows == [[RED, GREEN, BLUE], [WHITE, RED, GREEN]]


def test_bitfields_layout_is_handled():
    _, _, _, rows = decode_png(dib_to_png(make_dib(3, 2, 32, IMAGE, bitfields=True)))
    assert rows[0][2] == BLUE + (255,)


@pytest.mark.parametrize("dib", [
    b"",
    b"\x00" * 30,
    make_dib(3, 2, 32, IMAGE)[:60],                                   # truncated pixels
    struct.pack("<IiiHHIIiiII", 40, 3, 2, 1, 8, 0, 0, 0, 0, 0, 0) + b"\x00" * 64,  # 8-bit palette
])
def test_unreadable_bitmaps_return_none_instead_of_crashing(dib):
    assert dib_to_png(dib) is None


def test_a_screenshot_sized_bitmap_converts_quickly():
    import time
    w, h = 1920, 1080
    row = bytes([10, 20, 30, 0]) * w
    dib = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 0, 0, 0, 0, 0, 0) + row * h
    start = time.perf_counter()
    png = dib_to_png(dib)
    took = time.perf_counter() - start
    assert png_size(png) == (1920, 1080)
    assert took < 3, f"a 1080p screenshot took {took:.2f}s to convert"


def test_windows_synthesised_v5_bitmap_with_repeated_masks():
    """Exactly what Windows 11 handed over on a real machine for a copied
    160x90 image: a 124-byte BITMAPV5HEADER with BI_BITFIELDS masks inside it,
    the same 12 mask bytes repeated after the header, then bottom-up pixels.
    Reading from byte 124 instead of 136 shifted the whole picture."""
    w, h = 4, 3
    masks = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    header = struct.pack("<IiiHHIIiiII", 124, w, h, 1, 32, 3, w * h * 4, 0, 0, 0, 0)
    header = header + masks + b"\x00" * (124 - len(header) - len(masks))
    assert len(header) == 124
    top = [RED, GREEN, BLUE, WHITE]
    rest = [WHITE, WHITE, WHITE, WHITE]
    rows_top_down = [top, rest, rest]
    body = b""
    for row in reversed(rows_top_down):  # stored bottom-up
        body += b"".join(bytes([b, g, r, 0]) for r, g, b in row)
    dib = header + masks + body
    _, _, _, rows = decode_png(dib_to_png(dib))
    assert rows[0] == [RED + (255,), GREEN + (255,), BLUE + (255,), WHITE + (255,)], \
        "pixels are shifted, the repeated masks were read as image data"
    assert rows[2][0] == WHITE + (255,)


def test_v5_bitmap_without_repeated_masks_is_not_shifted():
    """And the opposite: a V5 header with nothing after it must not skip 12 bytes."""
    w, h = 4, 2
    masks = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    header = struct.pack("<IiiHHIIiiII", 124, w, h, 1, 32, 3, 0, 0, 0, 0, 0)
    header = header + masks + b"\x00" * (124 - len(header) - len(masks))
    rows_top_down = [[RED, GREEN, BLUE, WHITE], [BLUE, BLUE, BLUE, BLUE]]
    body = b"".join(bytes([b, g, r, 0]) for row in reversed(rows_top_down) for r, g, b in row)
    _, _, _, rows = decode_png(dib_to_png(header + body))
    assert rows[0][0] == RED + (255,) and rows[1][0] == BLUE + (255,)


def test_windows_synthesised_dib_that_windows_itself_shifted():
    """CF_DIB as Windows generated it from the V5 bitmap on a real machine:
    40-byte header, the masks, the masks again where pixels should start, and
    the pixel data 12 bytes short. The picture must still come out right."""
    w, h = 4, 2
    masks = struct.pack("<III", 0x00FF0000, 0x0000FF00, 0x000000FF)
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 32, 3, w * h * 4, 0, 0, 0, 0)
    rows_top_down = [[RED, GREEN, BLUE, WHITE], [BLUE, BLUE, BLUE, BLUE]]
    body = b"".join(bytes([b, g, r, 0]) for row in reversed(rows_top_down) for r, g, b in row)
    dib = header + masks + masks + body[:-12]  # what Windows actually hands over
    _, _, _, rows = decode_png(dib_to_png(dib))
    assert rows[1] == [BLUE + (255,)] * 4, "bottom row shifted"
    assert rows[0][0] == RED + (255,), "top row shifted"
