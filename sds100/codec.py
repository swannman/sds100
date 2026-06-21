"""Low-level codec for Uniden Sentinel ``.hpe`` favorites-export files.

A ``.hpe`` file produced by the BCDx36HP / SDS100 "Sentinel" software is::

    scramble( gzip( <tab-delimited text> + signature ) )

where ``scramble`` is a byte-wise XOR with the constant ``0x0C`` (it is its
own inverse), the gzip stream is a standard RFC-1952 member, and the trailing
*signature* line is ``File\\tHomePatrol Export File`` terminated by CRLF.

This module deals only with the byte-level framing.  The structured record
text it produces/consumes is handled by :mod:`sds100.model`.

Reverse-engineered from ``BCDx36HP_Sentinel.exe`` (HomePatrolExportFile /
GzCompression / FileLib classes), Sentinel version 3.00.01.
"""

from __future__ import annotations

import gzip
import io

SCRAMBLE_KEY = 0x0C
SIGNATURE = "File\tHomePatrol Export File"
CRLF = "\r\n"


def _scramble(data: bytes) -> bytes:
    """XOR every byte with the scramble key.  Self-inverse."""
    return bytes(b ^ SCRAMBLE_KEY for b in data)


def decode(raw: bytes) -> str:
    """Decode raw ``.hpe`` bytes to the inner tab-delimited text.

    The text retains its original CRLF line endings, including the trailing
    signature line.
    """
    plain = _scramble(raw)
    if plain[:2] != b"\x1f\x8b":
        raise ValueError(
            "Not a valid .hpe file: gzip magic missing after de-scramble "
            f"(got {plain[:2].hex()})"
        )
    text = gzip.decompress(plain)
    return text.decode("utf-8")


def encode(text: str) -> bytes:
    """Encode inner tab-delimited text into raw ``.hpe`` bytes.

    ``text`` must already include its trailing signature line.  Use
    :func:`read` / :func:`write` for whole-file round trips and
    :mod:`sds100.model` to build the text.

    The gzip member is written with ``mtime=0`` for reproducible output.
    """
    buf = io.BytesIO()
    # mtime=0 -> deterministic; matches Sentinel's zeroed mtime field.
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(text.encode("utf-8"))
    return _scramble(buf.getvalue())


def has_signature(text: str) -> bool:
    return SIGNATURE in text.splitlines()


def read(path: str) -> str:
    """Read a ``.hpe`` file from disk and return its inner text."""
    with open(path, "rb") as fh:
        text = decode(fh.read())
    if not has_signature(text):
        raise ValueError(f"{path}: missing HomePatrol Export File signature")
    return text


def write(path: str, text: str) -> None:
    """Write inner text to ``path`` as a ``.hpe`` file.

    The signature line is appended automatically if not already present.
    """
    if not has_signature(text):
        if not text.endswith(CRLF):
            text += CRLF
        text += SIGNATURE + CRLF
    with open(path, "wb") as fh:
        fh.write(encode(text))
