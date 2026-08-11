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

    def judge(self, original: str, candidate: str) -> JudgeResult:
        return JudgeResult(accept=True, reason="null judge (scaffold)")


class OllamaChat:
    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(self, system: str, user: str) -> str:
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": {"temperature": 0.7},
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


class OllamaGenerator(Generator):
    def __init__(self, model: str = "llama3.2", base_url: str = "http://127.0.0.1:11434"):
        self.model_id = f"ollama:{model}"
        self._client = OllamaChat(model=model, base_url=base_url)

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


class OllamaJudge(Judge):
    def __init__(self, model: str = "llama3.2", base_url: str = "http://127.0.0.1:11434"):
        self.model_id = f"ollama:{model}"
        self._client = OllamaChat(model=model, base_url=base_url)
        # colder for judging
        self._client.timeout = 60.0

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


def _parse_string_list(raw: str, k: int) -> List[str]:
    raw = raw.strip()
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
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return JudgeResult(
            accept=bool(data.get("accept")),
            reason=str(data.get("reason") or ""),
        )
    except json.JSONDecodeError:
        low = raw.lower()
        if "accept" in low and "true" in low and "false" not in low.split("accept", 1)[-1][:20]:
            return JudgeResult(accept=True, reason=raw[:200])
        return JudgeResult(accept=False, reason=f"unparseable judge output: {raw[:200]}")


def make_generator(kind: str = "auto", model: Optional[str] = None) -> Generator:
    from .embed import ollama_available

    kind = (kind or "auto").lower()
    if kind == "null":
        return NullGenerator()
    if kind == "ollama" or (kind == "auto" and ollama_available()):
        return OllamaGenerator(
            model=model or os.environ.get("PROSEVARY_GEN_MODEL", "llama3.2")
        )
    return NullGenerator()


def make_judge(kind: str = "auto", model: Optional[str] = None) -> Judge:
    from .embed import ollama_available

    kind = (kind or "auto").lower()
    if kind == "null":
        return NullJudge()
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
    return NullJudge()
