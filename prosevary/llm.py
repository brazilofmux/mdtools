"""
Local LLM backends for generate (paraphrase) and judge (accept/reject).

Default: Ollama chat API. Scaffold falls back to a no-op that declines
generation so synonym mode can still run offline.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class JudgeResult:
    accept: bool
    reason: str


class Generator(ABC):
    model_id: str

    @abstractmethod
    def paraphrase(self, sentence: str, freeze_terms: List[str], k: int = 3) -> List[str]:
        ...


class Judge(ABC):
    model_id: str
    # False when the judge rubber-stamps rather than actually assessing, so
    # callers can report which gates really ran.
    enforcing: bool = True

    @abstractmethod
    def judge(self, original: str, candidate: str) -> JudgeResult:
        ...


class NullGenerator(Generator):
    """Offline: produces no LLM candidates."""

    model_id = "null"

    def paraphrase(self, sentence: str, freeze_terms: List[str], k: int = 3) -> List[str]:
        return []


class NullJudge(Judge):
    """Offline: accepts everything that reached the judge (freeze+tau already passed)."""

    model_id = "null-accept"
    enforcing = False

    def judge(self, original: str, candidate: str) -> JudgeResult:
        return JudgeResult(accept=True, reason="null judge (scaffold)")


class OllamaChat:
    """Ollama native /api/chat."""

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
        temperature: float = 0.7,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": self.temperature},
        }
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return (data.get("message") or {}).get("content") or ""


class OpenAIChat:
    """
    OpenAI-compatible /v1/chat/completions.

    Covers mlx_lm.server (default port 8080), llama.cpp --server, LM Studio,
    and vLLM. Nothing here is MLX-specific on purpose.
    """

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:8080",
        timeout: float = 120.0,
        temperature: float = 0.7,
        api_key: Optional[str] = None,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.temperature = temperature
        # Reasoning models spend most of the budget thinking before the answer:
        # a Qwen3 judge verdict measured ~236 completion tokens for one short
        # sentence. Never inherit the server default — too small a cap returns
        # empty content, which the judge parser would read as a silent reject.
        self.max_tokens = max_tokens
        # Local servers ignore auth; kept so a remote endpoint works unchanged.
        self.api_key = api_key or os.environ.get("PROSEVARY_API_KEY", "")

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        choices = data.get("choices") or []
        if not choices:
            return ""
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content") or ""
        if not content.strip() and choice.get("finish_reason") == "length":
            # Servers that split thinking into its own field (mlx_lm.server) can
            # return no content at all. Fail loudly: returning "" here would be
            # parsed as a reject and quietly poison every verdict in the run.
            raise RuntimeError(
                f"{self.model} produced no content in {self.max_tokens} tokens "
                f"(finish_reason=length"
                + (", all spent on reasoning" if msg.get("reasoning") else "")
                + "). Raise max_tokens or disable thinking on the server."
            )
        return content


class ChatGenerator(Generator):
    """Paraphrase over any client exposing chat(system, user) -> str."""

    def __init__(self, client, model_id: str):
        self._client = client
        self.model_id = model_id

    def paraphrase(self, sentence: str, freeze_terms: List[str], k: int = 3) -> List[str]:
        freeze = ", ".join(repr(t) for t in freeze_terms[:40]) or "(none)"
        system = (
            "You rewrite single English sentences for lexical variety. "
            "Preserve meaning exactly. Preserve technical terms, code, paths, "
            "and any freeze list tokens character-for-character. "
            "Keep a sharp, first-person technical-narrative voice — not corporate, "
            "not textbook. Output ONLY a JSON array of strings, length "
            f"{k}, no markdown fences."
        )
        user = (
            f"Freeze terms (must appear unchanged if present): {freeze}\n\n"
            f"Sentence:\n{sentence}\n"
        )
        raw = self._client.chat(system, user)
        return _parse_string_list(raw, k)


class ChatJudge(Judge):
    """Accept/reject over any client exposing chat(system, user) -> str."""

    def __init__(self, client, model_id: str):
        self._client = client
        self.model_id = model_id

    def judge(self, original: str, candidate: str) -> JudgeResult:
        system = (
            "You are a pedantic editor for a technical memoir. "
            "Accept a candidate only if: (1) meaning is unchanged, "
            "(2) no technical claim is altered, (3) no quotation or code is altered, "
            "(4) voice stays concrete and non-corporate. "
            'Reply with JSON only: {"accept": true|false, "reason": "short"}'
        )
        user = f"ORIGINAL:\n{original}\n\nCANDIDATE:\n{candidate}\n"
        raw = self._client.chat(system, user)
        return _parse_judge(raw)


OLLAMA_URL = "http://127.0.0.1:11434"
# mlx_lm.server defaults to 8080; llama.cpp and LM Studio commonly use it too.
OPENAI_URL = "http://127.0.0.1:8080"


class OllamaGenerator(ChatGenerator):
    def __init__(self, model: str = "llama3.2", base_url: str = OLLAMA_URL):
        super().__init__(OllamaChat(model=model, base_url=base_url), f"ollama:{model}")


class OllamaJudge(ChatJudge):
    def __init__(self, model: str = "llama3.2", base_url: str = OLLAMA_URL):
        # Judging is a classification, not a creative task: cold and short.
        super().__init__(
            OllamaChat(model=model, base_url=base_url, timeout=60.0, temperature=0.0),
            f"ollama:{model}",
        )


class OpenAIGenerator(ChatGenerator):
    def __init__(self, model: str, base_url: str = OPENAI_URL):
        super().__init__(OpenAIChat(model=model, base_url=base_url), f"openai:{model}")


class OpenAIJudge(ChatJudge):
    def __init__(self, model: str, base_url: str = OPENAI_URL):
        super().__init__(
            OpenAIChat(model=model, base_url=base_url, timeout=60.0, temperature=0.0),
            f"openai:{model}",
        )


def openai_available(
    base_url: str = OPENAI_URL,
    timeout: float = 1.5,
    api_key: Optional[str] = None,
) -> bool:
    """
    Probe GET /v1/models. Sends Bearer auth when an API key is configured so
    remote endpoints that require it are not reported as "down".
    """
    try:
        headers = {}
        key = api_key if api_key is not None else os.environ.get("PROSEVARY_API_KEY", "")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        req = urllib.request.Request(
            f"{base_url.rstrip('/')}/v1/models", method="GET", headers=headers
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def resolve_openai_model(cli_model: Optional[str], env_name: str) -> str:
    """CLI flag, then env, then empty (caller must reject empty for openai)."""
    return (cli_model or os.environ.get(env_name) or "").strip()


# Qwen3 and friends emit <think>…</think> before the answer, which is not JSON.
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# An unclosed block means the model ran out of tokens mid-reasoning.
_THINK_OPEN = re.compile(r"<think>.*$", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(raw: str) -> str:
    raw = _THINK.sub("", raw)
    raw = _THINK_OPEN.sub("", raw)
    return raw.strip()


def _parse_string_list(raw: str, k: int) -> List[str]:
    raw = _strip_reasoning(raw)
    # Strip ```json fences if the model misbehaves. Case-insensitive: a ```JSON
    # fence otherwise left the bare word behind, and the line fallback then fed
    # the literal string "JSON" into the pipeline as a paraphrase candidate.
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()][:k]
    except json.JSONDecodeError:
        pass
    # fallback: lines
    lines = [ln.strip("-• \t") for ln in raw.splitlines() if ln.strip()]
    return [ln for ln in lines if ln][:k]


def _verdict_from_mapping(data: dict) -> Optional[JudgeResult]:
    """
    Fail-closed verdict from a JSON object.

    Only the literal boolean true accepts. Literal false rejects. Any other
    accept type (string "false", 1, null, …) rejects — bool("false") is True
    in Python and was the original footgun. Returns None when the object has
    no accept field so the caller can try another blob or reject.
    """
    if "accept" not in data:
        return None
    reason = data.get("reason")
    reason_s = "" if reason is None else str(reason)
    accept = data["accept"]
    if accept is True:
        return JudgeResult(accept=True, reason=reason_s)
    if accept is False:
        return JudgeResult(accept=False, reason=reason_s)
    return JudgeResult(
        accept=False,
        reason=(
            f"non-boolean accept ({type(accept).__name__}): {accept!r}"
            + (f" — {reason_s}" if reason_s else "")
        )[:200],
    )


def _json_objects(raw: str) -> List[dict]:
    """
    Every JSON object embedded in raw, outermost-first, in source order.

    A regex cannot do this. `\\{[^{}]*\\}` fails on an object containing a
    nested object or a brace inside a string, so a judge answering
    `Final: {"accept": true, "meta": {"n": 1}}` was read as unparseable and
    rejected. raw_decode parses from each '{' and reports where the value
    ended, which handles nesting and quoted braces for free.
    """
    decoder = json.JSONDecoder()
    found: List[dict] = []
    idx = 0
    while True:
        start = raw.find("{", idx)
        if start < 0:
            return found
        try:
            obj, end = decoder.raw_decode(raw, start)
        except ValueError:
            # Not the start of a valid value — step past it and keep looking.
            idx = start + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
            idx = end  # skip its interior; we want outermost objects
        else:
            idx = start + 1


def _parse_judge(raw: str) -> JudgeResult:
    """
    Parse a judge reply. Accept only a JSON object whose accept field is the
    literal boolean true. Everything else rejects without raising.
    """
    raw = _strip_reasoning(raw)
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        data = json.loads(raw)
        parsed_top_level = True
    except json.JSONDecodeError:
        data = None
        parsed_top_level = False

    if parsed_top_level:
        if isinstance(data, dict):
            verdict = _verdict_from_mapping(data)
            if verdict is not None:
                return verdict
            return JudgeResult(
                accept=False,
                reason=f"judge object missing accept: {raw[:200]}",
            )
        # Top-level array, string, null, bool, number — not a judge object.
        # (null parses as None; do not confuse with "JSON failed to parse".)
        kind = "null" if data is None else type(data).__name__
        return JudgeResult(
            accept=False,
            reason=f"judge JSON is not an object: {kind}",
        )

    # Not top-level JSON. A reasoning model often wraps the object in prose;
    # take the last {...} that carries an accept field, under the same type
    # rules. Ambiguous prose without a typed object always rejects.
    for candidate in reversed(_json_objects(raw)):
        verdict = _verdict_from_mapping(candidate)
        if verdict is not None:
            return verdict
    return JudgeResult(accept=False, reason=f"unparseable judge output: {raw[:200]}")


def make_generator(
    kind: str = "auto",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Generator:
    from .embed import ollama_available, ollama_has_model

    kind = (kind or "auto").lower()
    if kind == "null":
        return NullGenerator()
    if kind == "openai":
        resolved = resolve_openai_model(model, "PROSEVARY_GEN_MODEL")
        if not resolved:
            raise ValueError(
                "OpenAI generator requires --gen-model or $PROSEVARY_GEN_MODEL"
            )
        return OpenAIGenerator(
            model=resolved,
            base_url=base_url or OPENAI_URL,
        )
    gen_model = model or os.environ.get("PROSEVARY_GEN_MODEL", "llama3.2")
    if kind == "ollama":
        return OllamaGenerator(model=gen_model)
    # auto: require the model to be pulled, not merely the server to be up.
    # Checking only ollama_available() picked llama3.2 on a host where the
    # user had pulled nomic-embed-text for embeddings and nothing else, and
    # the 404 surfaced as an unhandled HTTPError from the first paraphrase —
    # the very traceback the preflight exists to prevent.
    if kind == "auto" and ollama_available() and ollama_has_model(gen_model):
        return OllamaGenerator(model=gen_model)
    # auto: an OpenAI-compatible server is only used if one is actually up.
    if kind == "auto" and openai_available(base_url or OPENAI_URL):
        resolved = resolve_openai_model(model, "PROSEVARY_GEN_MODEL")
        if resolved:
            return OpenAIGenerator(
                model=resolved,
                base_url=base_url or OPENAI_URL,
            )
    return NullGenerator()


def make_judge(
    kind: str = "auto",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Judge:
    from .embed import ollama_available, ollama_has_model

    kind = (kind or "auto").lower()
    if kind == "null":
        return NullJudge()
    if kind == "openai":
        resolved = resolve_openai_model(model, "PROSEVARY_JUDGE_MODEL")
        if not resolved:
            raise ValueError(
                "OpenAI judge requires --judge-model or $PROSEVARY_JUDGE_MODEL"
            )
        return OpenAIJudge(
            model=resolved,
            base_url=base_url or OPENAI_URL,
        )
    if kind == "null-reject":
        class _R(Judge):
            model_id = "null-reject"

            def judge(self, original: str, candidate: str) -> JudgeResult:
                return JudgeResult(accept=False, reason="null-reject")

        return _R()
    judge_model = model or os.environ.get("PROSEVARY_JUDGE_MODEL", "llama3.2")
    if kind == "ollama":
        return OllamaJudge(model=judge_model)
    # auto: same rule as the generator — the model must actually be pulled.
    if kind == "auto" and ollama_available() and ollama_has_model(judge_model):
        return OllamaJudge(model=judge_model)
    if kind == "auto" and openai_available(base_url or OPENAI_URL):
        resolved = resolve_openai_model(model, "PROSEVARY_JUDGE_MODEL")
        if resolved:
            return OpenAIJudge(
                model=resolved,
                base_url=base_url or OPENAI_URL,
            )
    return NullJudge()


# Pairs a competent judge must reject. Each changes a technical claim while
# staying fluent and preserving every freeze term, so freeze and tau cannot
# catch them — the judge is the only gate that can. A model that accepts
# these is a NullJudge with latency.
JUDGE_PROBES: List[tuple] = [
    (
        "We use a significant amount of tooling to demonstrate the approach.",
        "We use a significant amount of tooling to prove the approach.",
        False,
        "demonstrate -> prove: showing is not establishing correctness",
    ),
    (
        "The linker requires three additional passes.",
        "The linker requires four additional passes.",
        False,
        "number changed",
    ),
    (
        "The benchmark failed on the second run.",
        "The benchmark passed on the second run.",
        False,
        "outcome negated",
    ),
    (
        "Relocations are resolved before the final link step.",
        "Relocations are resolved after the final link step.",
        False,
        "ordering reversed",
    ),
    (
        "We use a lot of tooling for this stage.",
        "We use plenty of tooling for this stage.",
        True,
        "control: genuine paraphrase, should be accepted",
    ),
]


def probe_judge(judge: Judge) -> List[dict]:
    """
    Run JUDGE_PROBES through a judge. Returns one dict per probe.

    Purpose is to catch a rubber-stamping judge before it is trusted with -i,
    which matters most for compliance-tuned or abliterated models.
    """
    out: List[dict] = []
    for original, candidate, want_accept, note in JUDGE_PROBES:
        try:
            res = judge.judge(original, candidate)
            got, reason, err = res.accept, res.reason, ""
        except Exception as exc:  # network, timeout, bad payload
            got, reason, err = None, "", f"{type(exc).__name__}: {exc}"
        out.append(
            {
                "original": original,
                "candidate": candidate,
                "want_accept": want_accept,
                "got_accept": got,
                "correct": (got == want_accept) if got is not None else False,
                "reason": reason,
                "error": err,
                "note": note,
            }
        )
    return out
