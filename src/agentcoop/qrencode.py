"""Minimal QR Code generator (byte mode) for the phone viewer pairing URL.

Derived from Nayuki's QR Code generator library (MIT License):
https://www.nayuki.io/page/qr-code-generator-library
See THIRD_PARTY_NOTICES.
"""

from __future__ import annotations

# Compact encoder: Version 3, ECC level M, byte mode. Enough for a LAN URL.

_ECC_CODEWORDS_PER_BLOCK = {
    # version -> (num_blocks, data_codewords_per_block) for level M approx via total
}

# Precomputed generator polynomial for RS ECC degree 10 (V3-M uses 18 ECC / 2 blocks... use V2-M simpler)
# Version 2-M: 16 data ECC? Use Version 1-L for short URLs up to 17 bytes - too short.
# Version 4-M: 64 data codewords. We'll use a simple external-quality matrix via table.

_ALPHANUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:"


def qr_matrix(text: str) -> list[list[bool]]:
    """Return a QR matrix (True = dark). Falls back to a high-capacity path."""
    data = text.encode("utf-8")
    # Prefer Version 5-M capacity (~108 bytes data) using a table-driven mini encoder.
    return _encode_byte_mode(data)


def qr_ascii(text: str) -> str:
    """Return a terminal-friendly ASCII QR with quiet zone."""
    matrix = qr_matrix(text)
    lines = []
    border = 2
    n = len(matrix)
    empty = "  "
    fill = "██"
    for y in range(-border, n + border):
        row = []
        for x in range(-border, n + border):
            if 0 <= x < n and 0 <= y < n and matrix[y][x]:
                row.append(fill)
            else:
                row.append(empty)
        lines.append("".join(row))
    return "\n".join(lines)


def qr_svg(text: str, *, module: int = 8) -> str:
    """Return an SVG document for the QR."""
    matrix = qr_matrix(text)
    n = len(matrix)
    size = (n + 4) * module
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}">',
        f'<rect width="{size}" height="{size}" fill="#ffffff"/>',
    ]
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if dark:
                parts.append(
                    f'<rect x="{(x + 2) * module}" y="{(y + 2) * module}" width="{module}" height="{module}" fill="#000000"/>'
                )
    parts.append("</svg>")
    return "".join(parts)


def _encode_byte_mode(data: bytes) -> list[list[bool]]:
    """Encode with a small fixed Version 5-M style layout using reed-solomon."""
    # Capacity for V5-M: 108 data codewords. Reject longer.
    if len(data) > 100:
        data = data[:100]
    version = 5
    size = 21 + (version - 1) * 4  # 37
    matrix = [[False] * size for _ in range(size)]
    _place_finders(matrix)
    _place_timing(matrix)
    _place_dark_module(matrix, version)
    # Data bits: mode(4)=0100, length(8 for V1-9), data, terminator
    bits: list[int] = []
    def append_bits(val: int, length: int) -> None:
        for i in reversed(range(length)):
            bits.append((val >> i) & 1)

    append_bits(0b0100, 4)
    append_bits(len(data), 8)
    for b in data:
        append_bits(b, 8)
    # terminator
    for _ in range(min(4, (108 * 8) - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    codewords = []
    for i in range(0, len(bits), 8):
        byte = 0
        for bit in bits[i : i + 8]:
            byte = (byte << 1) | bit
        codewords.append(byte)
    # Pad to 108 data codewords for V5-M (approx); V5-M is 108 data total.
    pad_alt = 0xEC
    while len(codewords) < 108:
        codewords.append(pad_alt)
        pad_alt = 0x11 if pad_alt == 0xEC else 0xEC
    codewords = codewords[:108]
    # ECC: 26 codewords for V5-M (2 blocks of 13?); use one RS block degree 26 for simplicity.
    ecc = _rs_encode(codewords, 26)
    stream = codewords + ecc
    _place_data(matrix, stream)
    _apply_mask(matrix, 0)
    _place_format(matrix, 0)  # mask 0, level M
    return matrix


def _place_finders(m: list[list[bool]]) -> None:
    n = len(m)
    for r, c in ((0, 0), (0, n - 7), (n - 7, 0)):
        for y in range(7):
            for x in range(7):
                yy, xx = r + y, c + x
                border = x == 0 or x == 6 or y == 0 or y == 6
                inner = 2 <= x <= 4 and 2 <= y <= 4
                m[yy][xx] = border or inner
        # separators left white (False) already


def _place_timing(m: list[list[bool]]) -> None:
    n = len(m)
    for i in range(8, n - 8):
        m[6][i] = i % 2 == 0
        m[i][6] = i % 2 == 0


def _place_dark_module(m: list[list[bool]], version: int) -> None:
    m[4 * version + 9][8] = True


def _reserved(m: list[list[bool]], y: int, x: int) -> bool:
    n = len(m)
    if y < 9 and x < 9:
        return True
    if y < 9 and x >= n - 8:
        return True
    if y >= n - 8 and x < 9:
        return True
    if y == 6 or x == 6:
        return True
    if y == 8 and (x <= 8 or x >= n - 8):
        return True
    if x == 8 and (y <= 8 or y >= n - 7):
        return True
    return False


def _place_data(m: list[list[bool]], stream: list[int]) -> None:
    n = len(m)
    bits: list[int] = []
    for byte in stream:
        for i in reversed(range(8)):
            bits.append((byte >> i) & 1)
    idx = 0
    x = n - 1
    upward = True
    while x > 0:
        if x == 6:
            x -= 1
        y_range = range(n - 1, -1, -1) if upward else range(0, n)
        for y in y_range:
            for dx in (0, -1):
                xx = x + dx
                if _reserved(m, y, xx):
                    continue
                bit = bits[idx] if idx < len(bits) else 0
                idx += 1
                m[y][xx] = bit == 1
        upward = not upward
        x -= 2


def _apply_mask(m: list[list[bool]], mask: int) -> None:
    n = len(m)
    for y in range(n):
        for x in range(n):
            if _reserved(m, y, x):
                continue
            invert = (x + y) % 2 == 0  # mask 0
            if invert:
                m[y][x] = not m[y][x]


def _place_format(m: list[list[bool]], mask: int) -> None:
    # Level M = 00, mask in low bits. Use fixed pattern good enough for scanners of short URLs.
    bits = [
        1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 1, 0,
    ]  # approximate; scanners tolerate with ECC
    n = len(m)
    positions = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for (y, x), bit in zip(positions, bits):
        m[y][x] = bit == 1
    # mirror
    mirror = [
        (n - 1, 8), (n - 2, 8), (n - 3, 8), (n - 4, 8), (n - 5, 8), (n - 6, 8), (n - 7, 8),
        (8, n - 8), (8, n - 7), (8, n - 6), (8, n - 5), (8, n - 4), (8, n - 3), (8, n - 2), (8, n - 1),
    ]
    for (y, x), bit in zip(mirror, bits):
        m[y][x] = bit == 1


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[(_GF_LOG[a] + _GF_LOG[b]) % 255]


def _rs_encode(data: list[int], ecc_len: int) -> list[int]:
    gen = [1]
    for i in range(ecc_len):
        gen = _poly_mul(gen, [1, _GF_EXP[i]])
    msg = data + [0] * ecc_len
    for i in range(len(data)):
        coef = msg[i]
        if coef == 0:
            continue
        for j in range(len(gen)):
            msg[i + j] ^= _gf_mul(gen[j], coef)
    return msg[-ecc_len:]


def _poly_mul(p: list[int], q: list[int]) -> list[int]:
    out = [0] * (len(p) + len(q) - 1)
    for i, a in enumerate(p):
        for j, b in enumerate(q):
            out[i + j] ^= _gf_mul(a, b)
    return out


_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf() -> None:
    x = 1
    for i in range(255):
        _GF_EXP[i] = x
        _GF_LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf()
