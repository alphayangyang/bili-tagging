"""Fetch and parse Bilibili danmaku using only the Python standard library."""
from __future__ import annotations

import gzip
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Iterable

from .types import Danmaku

API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_DM = "https://api.bilibili.com/x/v1/dm/list.so"
API_SEG = "https://api.bilibili.com/x/v2/dm/web/seg.so"
SEGMENT_SECONDS = 360  # each seg.so segment covers six minutes of video
EMPTY_SEGMENT_BYTES = 64  # out-of-range segments come back as a tiny empty protobuf
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; danmaku-filter-research/0.1)",
    "Referer": "https://www.bilibili.com/",
}
TIMEOUT_SECONDS = 15


def http_get(url: str, params: dict | None = None, timeout: int = TIMEOUT_SECONDS) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def get_cid(bvid: str) -> int:
    payload = json.loads(http_get(API_VIEW, {"bvid": bvid}))
    if payload.get("code") != 0:
        raise RuntimeError(f"Bilibili view API error: {payload}")
    return int(payload["data"]["cid"])


def decode_xml(raw: bytes) -> bytes:
    """Accept plain XML, raw deflate, zlib, or gzip payloads."""
    if raw.lstrip().startswith(b"<"):
        return raw
    for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            continue
    try:
        return gzip.decompress(raw)
    except OSError:
        return raw


DANMAKU_RE = re.compile(r'<d p="([^"]*)"[^>]*>(.*?)</d>', re.S)


def _unescape(text: str) -> str:
    return (
        text.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&amp;", "&")
    )


def parse_xml(raw: bytes) -> list[Danmaku]:
    xml = decode_xml(raw).decode("utf-8", errors="replace")
    items: list[Danmaku] = []
    for attrs, body in DANMAKU_RE.findall(xml):
        fields = attrs.split(",")
        time_seconds = float(fields[0]) if fields and fields[0] else 0.0
        mode = int(fields[1]) if len(fields) > 1 and fields[1] else 1
        uid = fields[6] if len(fields) > 6 else ""
        dmid = fields[7] if len(fields) > 7 else ""
        text = _unescape(body).strip()
        if text:
            items.append(
                Danmaku(text=text, time_ms=int(time_seconds * 1000), mode=mode, uid=uid, dmid=dmid)
            )
    return items


def fetch_danmaku(bvid: str) -> list[Danmaku]:
    cid = get_cid(bvid)
    return parse_xml(http_get(API_DM, {"oid": cid}))


def load_jsonl(path: str | Path) -> list[Danmaku]:
    fields = set(Danmaku.__dataclass_fields__)
    items: list[Danmaku] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        payload = json.loads(line)
        items.append(Danmaku(**{key: value for key, value in payload.items() if key in fields}))
    return items


def load_danmaku(path: str | Path) -> list[Danmaku]:
    path = Path(path)
    if path.suffix.lower() == ".xml":
        return parse_xml(path.read_bytes())
    return load_jsonl(path)


def save_jsonl(path: str | Path, rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# seg.so source (primary data source for the MVP)
# ---------------------------------------------------------------------------


def _http_get_raw(url: str, timeout: int = TIMEOUT_SECONDS) -> bytes:
    """GET raw bytes; a 304 Not Modified is treated as an empty segment."""
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code == 304:
            return b""
        raise


def get_video_info(bvid: str) -> dict:
    """Return {bvid, aid, cid, duration, title} for the video's first page."""
    payload = json.loads(http_get(API_VIEW, {"bvid": bvid}))
    if payload.get("code") != 0:
        raise RuntimeError(f"Bilibili view API error: {payload}")
    data = payload["data"]
    pages = data.get("pages") or []
    cid = int(pages[0]["cid"]) if pages else int(data["cid"])
    return {
        "bvid": bvid,
        "aid": int(data["aid"]),
        "cid": cid,
        "duration": int(data.get("duration") or 0),
        "title": str(data.get("title") or ""),
    }


def fetch_segment(aid: int, cid: int, index: int, retries: int = 3) -> bytes:
    """Fetch one six-minute danmaku segment (1-based); b"" means no more data."""
    url = f"{API_SEG}?{urllib.parse.urlencode({'type': 1, 'oid': cid, 'pid': aid, 'segment_index': index})}"
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            return _http_get_raw(url)
        except urllib.error.HTTPError as error:
            if error.code in (304, 404):
                return b""
            last_error = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = error
        time.sleep(min(2.0, 0.4 * (attempt + 1)))
    raise RuntimeError(f"failed to fetch segment {index} for cid {cid}") from last_error


def fetch_all_segments(
    aid: int, cid: int, duration: int, max_segments: int = 60
) -> list[bytes]:
    """Fetch every non-empty segment of a video, stopping at the first empty one."""
    if duration > 0:
        expected = max(1, math.ceil(duration / SEGMENT_SECONDS))
    else:
        expected = 1
    expected = min(expected, max_segments)
    segments: list[bytes] = []
    for index in range(1, expected + 1):
        raw = fetch_segment(aid, cid, index)
        if not raw or len(raw) < EMPTY_SEGMENT_BYTES:
            break
        segments.append(raw)
    return segments

