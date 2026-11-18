"""Minimal protobuf wire codec for Bilibili danmaku seg.so payloads.

Verified against the live API (see docs/design/plan-v1.md §2):

    Segment        = repeated DanmakuElem elem = 1;
    DanmakuElem    = 1 id (varint 64) | 2 progress (varint) | 3 mode (varint)
                   | 6 midHash (string) | 7 content (string) | 12 idStr (string)
                   | 26 cid (varint) | 27 type (varint) | ...unknown fields

We do not need the full .proto: elements are kept as raw bytes and only the
content field is spliced when collapsing, so unknown fields survive untouched.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .types import Danmaku

WIRE_VARINT = 0
WIRE_64BIT = 1
WIRE_LENGTH = 2
WIRE_32BIT = 5

ELEM_FIELD = 1
CONTENT_FIELD = 7

_ID = 1
_PROGRESS = 2
_MODE = 3
_MID_HASH = 6
_CONTENT = 7
_ID_STR = 12
_CID = 26


@dataclass(frozen=True)
class Field:
    """One parsed protobuf field; start/end cover the key plus the payload."""

    number: int
    wire_type: int
    value: bytes | int
    start: int
    end: int


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    for _ in range(10):
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
    raise ValueError("varint longer than 10 bytes")


def encode_varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("negative varints are not used by danmaku segments")
    out = bytearray()
    while True:
        chunk = value & 0x7F
        value >>= 7
        if value:
            out.append(chunk | 0x80)
        else:
            out.append(chunk)
            return bytes(out)


def parse_fields(buf: bytes) -> Iterator[Field]:
    """Yield every field of a protobuf message, preserving raw byte offsets."""
    pos = 0
    total = len(buf)
    while pos < total:
        start = pos
        key, pos = read_varint(buf, pos)
        number, wire_type = key >> 3, key & 0x07
        if number == 0:
            raise ValueError("invalid protobuf field number 0")
        if wire_type == WIRE_VARINT:
            value, pos = read_varint(buf, pos)
        elif wire_type == WIRE_LENGTH:
            length, pos = read_varint(buf, pos)
            end = pos + length
            if end > total:
                raise ValueError("truncated length-delimited field")
            value = buf[pos:end]
            pos = end
        elif wire_type == WIRE_32BIT:
            end = pos + 4
            if end > total:
                raise ValueError("truncated 32-bit field")
            value = buf[pos:end]
            pos = end
        elif wire_type == WIRE_64BIT:
            end = pos + 8
            if end > total:
                raise ValueError("truncated 64-bit field")
            value = buf[pos:end]
            pos = end
        else:
            raise ValueError(f"unsupported wire type {wire_type} (field {number})")
        yield Field(number, wire_type, value, start, pos)


def encode_field(number: int, payload: bytes) -> bytes:
    """Encode a length-delimited field (wire type 2)."""
    return encode_varint((number << 3) | WIRE_LENGTH) + encode_varint(len(payload)) + payload


def iter_elements(data: bytes) -> Iterator[bytes]:
    """Yield raw DanmakuElem payloads from a seg.so segment."""
    for field in parse_fields(data):
        if field.number == ELEM_FIELD and field.wire_type == WIRE_LENGTH:
            yield field.value  # type: ignore[misc]


def decode_element(raw: bytes) -> dict:
    """Decode the fields we care about from one DanmakuElem."""
    info: dict = {"text": "", "time_ms": 0, "mode": 1, "uid": "", "dmid": "", "cid": 0}
    id_value = 0
    id_str = ""
    for field in parse_fields(raw):
        if field.number == _ID and field.wire_type == WIRE_VARINT:
            id_value = int(field.value)
        elif field.number == _PROGRESS and field.wire_type == WIRE_VARINT:
            info["time_ms"] = int(field.value)
        elif field.number == _MODE and field.wire_type == WIRE_VARINT:
            info["mode"] = int(field.value)
        elif field.number == _MID_HASH and field.wire_type == WIRE_LENGTH:
            info["uid"] = bytes(field.value).decode("utf-8", errors="replace")
        elif field.number == _CONTENT and field.wire_type == WIRE_LENGTH:
            info["text"] = bytes(field.value).decode("utf-8", errors="replace")
        elif field.number == _ID_STR and field.wire_type == WIRE_LENGTH:
            id_str = bytes(field.value).decode("utf-8", errors="replace")
        elif field.number == _CID and field.wire_type == WIRE_VARINT:
            info["cid"] = int(field.value)
    info["dmid"] = id_str or (str(id_value) if id_value else "")
    return info


def to_danmaku(raw: bytes) -> Danmaku:
    info = decode_element(raw)
    return Danmaku(
        text=info["text"],
        time_ms=info["time_ms"],
        mode=info["mode"],
        dmid=info["dmid"],
        uid=info["uid"],
    )


def parse_segment(data: bytes) -> list[Danmaku]:
    """Decode one seg.so segment into Danmaku objects (empty text dropped)."""
    items: list[Danmaku] = []
    for raw in iter_elements(data):
        danmaku = to_danmaku(raw)
        if danmaku.text and danmaku.dmid:
            items.append(danmaku)
    return items


def replace_field(raw: bytes, number: int, payload: bytes) -> bytes:
    """Replace a length-delimited field inside a message, keeping all other bytes."""
    out = bytearray()
    replaced = False
    for field in parse_fields(raw):
        if field.number == number and field.wire_type == WIRE_LENGTH:
            out += encode_field(number, payload)
            replaced = True
        else:
            out += raw[field.start : field.end]
    if not replaced:
        raise KeyError(f"field {number} not found")
    return bytes(out)


def filter_segment(data: bytes, drop: set[str], collapse: dict[str, str]) -> tuple[bytes, int, int]:
    """Drop or collapse elements by dmid; returns (segment, hidden, collapsed)."""
    out = bytearray()
    hidden = 0
    collapsed = 0
    for field in parse_fields(data):
        if field.number == ELEM_FIELD and field.wire_type == WIRE_LENGTH:
            raw = bytes(field.value)
            dmid = decode_element(raw)["dmid"]
            if dmid in drop:
                hidden += 1
                continue
            if dmid in collapse:
                raw = replace_field(raw, CONTENT_FIELD, collapse[dmid].encode("utf-8"))
                collapsed += 1
            out += encode_field(ELEM_FIELD, raw)
        else:
            out += data[field.start : field.end]
    return bytes(out), hidden, collapsed
