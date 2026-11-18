"""LLM classifier for any OpenAI-compatible chat completions endpoint.

Works with cloud providers and with local runtimes that expose an
OpenAI-compatible API (Ollama, llama.cpp server, LM Studio, vLLM).

Required environment:
    DM_LLM_API_KEY    API key. Optional for local endpoints (localhost / private
                      IPv4 / .local): the key then defaults to "ollama".
Optional:
    DM_LLM_BASE_URL   default https://api.deepseek.com/v1
    DM_LLM_MODEL      default deepseek-flash
    DM_LLM_BATCH      comments per request, default 30
    DM_LLM_TIMEOUT    request timeout seconds, default 120 (local: 600)
    DM_LLM_RETRIES    extra attempts on network/5xx errors, default 2
    DM_LLM_ENV_FILE   dotenv file loaded before the variables above, default
                      <repo>/.env.local (git-ignored; point it at a missing path
                      to disable loading)
    DM_LLM_OLLAMA     1/0: use Ollama's native /api/chat instead of the OpenAI
                      path (auto-detected for local Ollama endpoints); the
                      native path can pass num_ctx per request, which avoids
                      loading a 40k context that does not fit in VRAM
    DM_LLM_NUM_CTX    context window for the native path, default 8192

The model returns a relevance vector: one score in [0, 1] per tag that actually
applies. Danmaku are often multi-label (a joke can also be a spoiler), and each
viewer picks their own per-tag thresholds. The label/confidence fields on the
result are the arg-maxing tag and its score, so single-label consumers keep
working.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Sequence

from ..taxonomy import (
    CATEGORY_IDS,
    FALLBACK_ID,
    prompt_guide,
    prompt_guide_v3,
    prompt_guide_v4,
)
from ..types import ClassificationResult, Danmaku
from .base import Classifier

SYSTEM_PROMPT = "You label Chinese Bilibili danmaku by social function. Return JSON only."

DEFAULT_BASE = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_FILE = REPO_ROOT / ".env.local"

KNOWN_TAGS = set(CATEGORY_IDS) | {FALLBACK_ID}
TAG_ORDER = {tag: index for index, tag in enumerate(list(CATEGORY_IDS) + [FALLBACK_ID])}


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE dotenv file; missing or unreadable gives {}."""
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env_file() -> None:
    """Load the dotenv file without overriding variables already set.

    Real environment variables win, so a shell export or a test patch always
    takes precedence over the file on disk.
    """
    custom = os.environ.get("DM_LLM_ENV_FILE")
    path = Path(custom) if custom else DEFAULT_ENV_FILE
    for key, value in _read_env_file(path).items():
        os.environ.setdefault(key, value)


def _is_local_host(host: str | None) -> bool:
    if not host:
        return False
    host = host.lower().strip("[]")
    if host in {"localhost", "::1", "host.docker.internal"} or host.endswith(".local"):
        return True
    if host.startswith("127.") or host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def settings() -> tuple[str, str, str]:
    load_env_file()
    base = os.environ.get("DM_LLM_BASE_URL", DEFAULT_BASE).rstrip("/")
    key = os.environ.get("DM_LLM_API_KEY", "")
    model = os.environ.get("DM_LLM_MODEL", DEFAULT_MODEL)
    if not key:
        if _is_local_host(urllib.parse.urlparse(base).hostname):
            key = "ollama"  # local runtimes ignore it, but the header is required
        else:
            raise RuntimeError(
                "DM_LLM_API_KEY is not set; use --backend rules, "
                "or point DM_LLM_BASE_URL at a local model"
            )
    return base, key, model


def timeout() -> float:
    raw = os.environ.get("DM_LLM_TIMEOUT")
    if raw:
        try:
            return max(5.0, float(raw))
        except ValueError:
            pass
    base = os.environ.get("DM_LLM_BASE_URL", DEFAULT_BASE)
    return 600.0 if _is_local_host(urllib.parse.urlparse(base).hostname) else 120.0


def temperature() -> float:
    raw = os.environ.get("DM_LLM_TEMP")
    if raw:
        try:
            return max(0.0, min(1.5, float(raw)))
        except ValueError:
            pass
    # Greedy decoding can make small local MoE models emit an empty answer on
    # some batches; a bit of sampling is much more stable there.
    base = os.environ.get("DM_LLM_BASE_URL", DEFAULT_BASE)
    return 0.3 if _is_local_host(urllib.parse.urlparse(base).hostname) else 0.0


def ollama_native() -> bool:
    """True when requests go to Ollama's native /api/chat instead of /v1.

    Ollama's OpenAI-compatible endpoint always loads the model with its default
    context length (40k for qwen3), which needs several gigabytes of KV cache.
    The native endpoint accepts num_ctx per request, so the same model fits on a
    small laptop GPU.
    """
    env = os.environ.get("DM_LLM_OLLAMA")
    if env is not None:
        return env.strip().lower() not in {"", "0", "false", "no", "off"}
    base = os.environ.get("DM_LLM_BASE_URL", DEFAULT_BASE)
    parsed = urllib.parse.urlparse(base)
    if not _is_local_host(parsed.hostname):
        return False
    return parsed.port in (11434, 11435) or "ollama" in (parsed.netloc or "")


def num_ctx() -> int:
    """Context window for the native Ollama path; 0 means leave it to the server."""
    raw = os.environ.get("DM_LLM_NUM_CTX")
    if raw:
        try:
            return max(512, int(raw))
        except ValueError:
            pass
    return 8192 if ollama_native() else 0


def retries() -> int:
    try:
        return max(0, int(os.environ.get("DM_LLM_RETRIES", "2")))
    except ValueError:
        return 2


def batch_size(default: int = 30) -> int:
    try:
        return max(1, int(os.environ.get("DM_LLM_BATCH", str(default))))
    except ValueError:
        return default


def prompt_style() -> str:
    """Prompt variant: v2 (flat), v3 (decision order), or v4 (tightened order, default).

    v3 added the ordered decision procedure from taxonomy.DECISION_ORDER with
    minimal-pair anchors. It fixed some information leaks but over-fired on plain
    laughter (emotional_resonance instead of humour) and on fact-carrying jokes
    (the anchor sentence forced different dominants, so the fact was dropped).
    v4 tightens the order (pure noise only, attack only for real hostility) and
    softens the anchor instruction to "scores should differ, dominant need not".
    Measured on 29 human-audited comments and 46 minimal-pair probes, v4 is the
    best of the three for the cloud teacher; see docs/experiments/e8-boundary.md.
    DM_LLM_PROMPT=v2|v3 restores the older behaviour for ablations.
    """
    return (os.environ.get("DM_LLM_PROMPT") or "v4").strip().lower()


def _clamp(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, score))


def _row_scores(row: dict) -> dict[str, float]:
    """Normalise one model row into {tag: score}.

    Accepts the vector format ({"scores": {...}}), falls back to the older
    single-label format ({"label": ..., "confidence": ...}) so old caches,
    prompts and tests keep working.
    """
    scores: dict[str, float] = {}
    raw = row.get("scores")
    if isinstance(raw, dict):
        for tag, value in raw.items():
            if isinstance(tag, str) and tag in KNOWN_TAGS:
                score = _clamp(value)
                if score > 0.0:
                    scores[tag] = score
    if not scores and "label" in row:
        label = str(row.get("label", ""))
        if label in KNOWN_TAGS:
            scores[label] = _clamp(row.get("confidence", 0.5))
        else:
            # An explicit but unknown label means the model answered badly;
            # keep confidence at 0 so the policy treats it as not-tagged.
            scores[FALLBACK_ID] = 0.0
    if not scores:
        scores[FALLBACK_ID] = 0.5
    return scores


def _dominant(scores: dict[str, float]) -> tuple[str, float]:
    """Highest-scoring tag; ties resolve in taxonomy order, other last."""
    best = max(scores.items(), key=lambda item: (item[1], -TAG_ORDER.get(item[0], 99)))
    return best[0], best[1]


class LLMClassifier(Classifier):
    name = "llm"

    def __init__(self, batch_size: int | None = None, video_context: str = "") -> None:
        self.batch_size = batch_size or globals()["batch_size"]()
        self.video_context = video_context

    def classify(self, dm: Danmaku) -> ClassificationResult:
        return self.classify_batch([dm])[0]

    def classify_batch(self, batch: Sequence[Danmaku]) -> list[ClassificationResult]:
        items = list(batch)
        results: list[ClassificationResult] = []
        for start in range(0, len(items), self.batch_size):
            results.extend(self._classify_chunk(items[start : start + self.batch_size]))
        return results

    def _classify_chunk(self, chunk: Sequence[Danmaku]) -> list[ClassificationResult]:
        rows: list[dict] | None = None
        last_error: Exception | None = None
        base_temp = temperature()
        for attempt in range(retries() + 1):
            # Escalating sampling makes empty/garbled greedy answers recoverable.
            body = self._request(chunk, temperature=min(0.9, base_temp + 0.3 * attempt))
            message = body["choices"][0]["message"]
            content = (message.get("content") or "").strip()
            if not content:
                last_error = RuntimeError("LLM returned empty content")
            else:
                try:
                    rows = self._extract_rows(content)
                    break
                except (json.JSONDecodeError, RuntimeError) as exc:
                    last_error = exc
            if attempt < retries():
                time.sleep(1.0 + attempt)
        if rows is None:
            raise RuntimeError(f"LLM did not return usable JSON: {last_error}")
        results = []
        for index, dm in enumerate(chunk):
            row = rows[index] if index < len(rows) and isinstance(rows[index], dict) else {}
            scores = _row_scores(row)
            label, confidence = _dominant(scores)
            results.append(
                ClassificationResult(
                    label=label,
                    confidence=confidence,
                    rationale=str(row.get("note") or row.get("rationale") or ""),
                    source=self.name,
                    tags=scores,
                )
            )
        return results

    def _request(self, chunk: Sequence[Danmaku], temperature: float | None = None) -> dict:
        base, key, model = settings()
        temp = globals()["temperature"]() if temperature is None else temperature
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._prompt(chunk)},
        ]
        if ollama_native():
            root = base[:-3] if base.endswith("/v1") else base
            options: dict = {"temperature": temp, "num_predict": max(256, 80 * len(chunk))}
            ctx = num_ctx()
            if ctx:
                options["num_ctx"] = ctx
            body = self._post(
                f"{root}/api/chat",
                {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    # No format="json": Ollama then constrains the answer to a
                    # single JSON value and small models drop all but the first
                    # row. The prompt already asks for a JSON array.
                    "options": options,
                },
            )
            content = (body.get("message") or {}).get("content") or ""
            return {"choices": [{"message": {"content": content}}]}
        return self._post(
            f"{base}/chat/completions",
            {"model": model, "temperature": temp, "messages": messages},
            key=key,
        )

    def _post(self, url: str, payload: dict, key: str | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        last_error: Exception | None = None
        for attempt in range(retries() + 1):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=timeout()) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    raise RuntimeError(f"LLM endpoint returned HTTP {exc.code}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < retries():
                time.sleep(min(2.0 * (attempt + 1), 10.0))
        raise RuntimeError(f"LLM request failed after {retries() + 1} attempts: {last_error}")

    def _prompt(self, chunk: Sequence[Danmaku]) -> str:
        comments = chr(10).join(
            "%d. [%ds] %s" % (index + 1, dm.time_ms // 1000, dm.text)
            for index, dm in enumerate(chunk)
        )
        context = (" Video context: " + self.video_context) if self.video_context else ""
        style = prompt_style()
        parts: list[str] = [
            "Score how strongly each Chinese Bilibili danmaku (bullet comment) expresses "
            "each social function. A comment can score high on several functions at once "
            "(a joke built on a fact keeps both).",
        ]
        if style in ("v3", "v4"):
            from ..boundary import fewshot_pairs

            parts.append("Functions and decision order:")
            parts.append(prompt_guide_v4() if style == "v4" else prompt_guide_v3())
            anchors = ["Boundary anchors, same topic but different function:"]
            for left, right in fewshot_pairs():
                anchors.append("  - %s  |  %s" % (left, right))
            parts.append(chr(10).join(anchors))
            if style == "v3":
                parts.append(
                    "For each anchor pair, the left comment and the right comment must not "
                    "receive the same dominant function."
                )
            else:
                parts.append(
                    "The anchors only show how the scores should differ; they are not a rule "
                    "that two comments must have different dominant functions. A joke that "
                    "carries a fact keeps information at 0.6 or higher even when humour is "
                    "its dominant function."
                )
        else:
            parts.append("Functions:")
            parts.append(prompt_guide())
        parts.append(
            "Scoring rules:"
        )
        parts.append(
            "- 0 = not this function at all, 1 = clearly and strongly this function."
        )
        parts.append("- Return at most 5 functions per comment and omit scores below 0.10.")
        parts.append(
            "- Be honest and conservative: most comments are low for most functions."
        )
        if style in ("v3", "v4"):
            parts.append(
                "- Apply the decision order first, then write scores. If a comment provides "
                "a fact and also jokes, information stays 0.6 or higher."
            )
        if style == "v4":
            parts.append(
                "- Laughter markers attached to a remark (笑死, 哈哈哈哈, 离谱, 好家伙) are "
                "humour, not emotional_resonance; resonance is a plain feeling about the video."
            )
            parts.append(
                "- Pure noise means nothing but symbols, repeats, digits or hype. If any word of "
                "commentary is attached, it is not pure noise."
            )
        parts.append(
            "- Humour needs a joke, pun, meme twist or absurd comparison. A plain positive "
            "reaction such as 太好听了 / 好感动 / 泪目 / 好听啊 is emotional_resonance, not humour."
        )
        parts.append(
            "- Symbol spam (？？？, 。。。。。., 1111, kkkk, pure emoji) is "
            "irrelevant_repetition at 0.8 or higher, never other."
        )
        parts.append(
            "- spoiler only when the comment reveals a concrete plot event or ending of the "
            "work being watched (who dies, who the killer is, a cameo). Merely naming a title "
            "together with 大结局 / 预告 / 剧透 as a joke or a meme is humour or other, not spoiler."
        )
        parts.append(
            "- other is a last resort, not a safe default: give other a high score only when "
            "every function above would score below 0.2 (off-topic chatter, inside jokes with "
            "no recoverable meaning). If a comment is merely ambiguous, give the closest "
            "function 0.2-0.4 and say why in the note."
        )
        parts.append(
            "Return ONLY a JSON array with exactly %d objects, one per comment, in the same "
            "order as the input, like "
            '[{"id": 1, "scores": {"humor": 0.9, "spoiler": 0.2}, "note": "短理由"}, ...].'
            % len(chunk)
        )
        parts.append("Comments:")
        parts.append(comments)
        return (chr(10).join(parts) + context)

    @staticmethod
    def _extract_rows(content: str) -> list[dict]:
        text = content.strip()
        if text.startswith("```"):
            first = text.find("\n")
            text = text[first + 1 :] if first != -1 else text[3:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            # a single object is a degenerate but recoverable answer for 1-item batches
            brace = text.find("{")
            if brace != -1 and text.rfind("}") > brace:
                # strict=False tolerates raw newlines/tabs inside rationale strings.
                obj = json.loads(text[brace : text.rfind("}") + 1], strict=False)
                return [obj] if isinstance(obj, dict) else []
            raise RuntimeError(f"LLM did not return a JSON array: {text[:200]!r}")
        rows = json.loads(text[start : end + 1], strict=False)
        if not isinstance(rows, list):
            raise RuntimeError("LLM response is not a JSON array")
        return rows
