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


def openai_available(base_url: str = OPENAI_URL, timeout: float = 1.5) -> bool:
    try:
        req = urllib.request.Request(f"{base_url.rstrip('/')}/v1/models", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


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
    # strip ```json fences if the model misbehaves
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
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


def _parse_judge(raw: str) -> JudgeResult:
    raw = _strip_reasoning(raw)
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return JudgeResult(
            accept=bool(data.get("accept")),
            reason=str(data.get("reason") or ""),
        )
    except json.JSONDecodeError:
        # A reasoning model often wraps the object in prose; take the last
        # {...} rather than giving up and defaulting to reject.
        objs = re.findall(r"\{[^{}]*\}", raw, re.DOTALL)
        for blob in reversed(objs):
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            if "accept" in data:
                return JudgeResult(
                    accept=bool(data.get("accept")),
                    reason=str(data.get("reason") or ""),
                )
        low = raw.lower()
        if "accept" in low and "true" in low and "false" not in low.split("accept", 1)[-1][:20]:
            return JudgeResult(accept=True, reason=raw[:200])
        return JudgeResult(accept=False, reason=f"unparseable judge output: {raw[:200]}")


def make_generator(
    kind: str = "auto",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Generator:
    from .embed import ollama_available

    kind = (kind or "auto").lower()
    if kind == "null":
        return NullGenerator()
    if kind == "openai":
        return OpenAIGenerator(
            model=model or os.environ.get("PROSEVARY_GEN_MODEL", ""),
            base_url=base_url or OPENAI_URL,
        )
    if kind == "ollama" or (kind == "auto" and ollama_available()):
        return OllamaGenerator(
            model=model or os.environ.get("PROSEVARY_GEN_MODEL", "llama3.2")
        )
    # auto: an OpenAI-compatible server is only used if one is actually up.
    if kind == "auto" and openai_available(base_url or OPENAI_URL):
        return OpenAIGenerator(
            model=model or os.environ.get("PROSEVARY_GEN_MODEL", ""),
            base_url=base_url or OPENAI_URL,
        )
    return NullGenerator()


def make_judge(
    kind: str = "auto",
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Judge:
    from .embed import ollama_available

    kind = (kind or "auto").lower()
    if kind == "null":
        return NullJudge()
    if kind == "openai":
        return OpenAIJudge(
            model=model or os.environ.get("PROSEVARY_JUDGE_MODEL", ""),
            base_url=base_url or OPENAI_URL,
        )
    if kind == "null-reject":
        class _R(Judge):
            model_id = "null-reject"

            def judge(self, original: str, candidate: str) -> JudgeResult:
                return JudgeResult(accept=False, reason="null-reject")

        return _R()
    if kind == "ollama" or (kind == "auto" and ollama_available()):
        return OllamaJudge(
            model=model or os.environ.get("PROSEVARY_JUDGE_MODEL", "llama3.2")
        )
    if kind == "auto" and openai_available(base_url or OPENAI_URL):
        return OpenAIJudge(
            model=model or os.environ.get("PROSEVARY_JUDGE_MODEL", ""),
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
