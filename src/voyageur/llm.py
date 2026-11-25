"""
===============================================================================
LLM Endpoints (any provider, one per role)
===============================================================================
Where a model call goes and how its request and reply are shaped. The pick step (which builds the prompt and
judges the answer) does not know providers; this module owns the wire format, so a pick works the same against a
local server or a cloud API. The model only ever answers with a reference to a listed element: nothing in this
module, or after it, treats model text as a locator.

A role is a kind of call: TEXT (pick an element from a quoted list) and VISION (a later stage: calls that carry a
marked screenshot). Each role resolves its own endpoint, so a vision model and a text model can live on different
providers, and an image only goes to a role that has been explicitly allowed to receive one.

Features:
    ✓ Providers: `openai` (any OpenAI-compatible /chat/completions: OpenAI, Azure, Gemini, OpenRouter, Ollama,
      LM Studio, vLLM, SGLang, LiteLLM, ...) and `anthropic` (the native Messages API)
    ✓ Per-role settings HEAL_<ROLE>_*; the text role also falls back to the plain HEAL_* names, so an existing setup
      keeps working. Vision never inherits a destination: unset means vision is unavailable
    ✓ The API key, headers and extra body never follow a role that points at another provider or base URL
    ✓ Extra headers and an extra body merged last; a key set to null is removed, so a provider that rejects a default
      field (temperature, max_tokens) can drop it
    ✓ Reasoning is off by default: HEAL_THINKING=off sends the Qwen / vLLM switches (" /no_think",
      chat_template_kwargs.enable_thinking=false, reasoning_effort="none"); HEAL_THINKING=on sends nothing
    ✓ Small answers: max_tokens defaults to 64 (a pick is one short reference), timeout 120 seconds
    ✓ Images as OpenAI `image_url` data-URI parts or Anthropic base64 `image` blocks, each named before it; refused
      unless the endpoint has images_allowed
    ✓ Attribution: X-Caller and X-Heal-Caller on every call and X-Heal-Id (the heal id) when one is given, so a gateway can charge
      a call to the heal that made it
    ✓ Replies: only the answer text is read (OpenAI message.content; Anthropic text blocks); an Anthropic refusal is
      no answer, with the reason. Usage comes back as integers (input_tokens, output_tokens, image_tokens)

Environment Variables (ROLE is TEXT or VISION; the text role falls back to the name on the right):
    HEAL_<ROLE>_PROVIDER       HEAL_PROVIDER       openai | anthropic (default openai)
    HEAL_<ROLE>_BASE_URL       HEAL_BASE_URL       openai: base URL incl. /v1; anthropic: optional, default
                                                   https://api.anthropic.com
    HEAL_<ROLE>_MODEL          HEAL_MODEL          model id, sent as given
    HEAL_<ROLE>_API_KEY        HEAL_API_KEY        openai: Authorization: Bearer; anthropic: x-api-key
    HEAL_<ROLE>_HEADERS        HEAL_HEADERS        JSON object of extra request headers (values are treated as secrets)
    HEAL_<ROLE>_EXTRA_BODY     HEAL_EXTRA_BODY     JSON object merged into the request body; null removes a key (it may
                                                   not set model or messages)
    HEAL_VISION_IMAGES_ALLOWED                     true = this endpoint may be sent images (default false)
    HEAL_<ROLE>_THINKING       HEAL_THINKING       off = send the Qwen / vLLM switches (openai only; default off);
                                                   on = send none
    HEAL_<ROLE>_MAX_TOKENS     HEAL_MAX_OUTPUT_TOKENS   max_tokens (default 64)
    HEAL_<ROLE>_TIMEOUT_S      HEAL_TIMEOUT_S      seconds per request (default 120)
    HEAL_CALLER                                    X-Caller and X-Heal-Caller on every call (default voyageur; empty = none)

Author: PMAC
===============================================================================
"""
import base64
import json
import os
import re
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

PROVIDERS = ("openai", "anthropic")
ROLES = ("text", "vision")
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
NO_THINK = " /no_think"   # Qwen's soft switch; only sent with thinking off

DEFAULT_MAX_TOKENS = 64      # a pick is one short reference to a listed element
DEFAULT_TIMEOUT_S = 120
DEFAULT_CALLER = "voyageur"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")   # a heal id goes into a header: nothing that could split it

Image = Tuple[str, bytes]  # (label shown to the model before the image, PNG bytes)


class LLMConfigError(ValueError):
    """A malformed LLM setting. The message names the variable, never its value (it may hold a key)."""


class ImagesNotAllowed(ValueError):
    """An image was about to be sent to an endpoint that has not been allowed to receive one."""


@dataclass(frozen=True)
class Endpoint:
    """One resolved LLM endpoint: everything needed to send a call for a role."""
    provider: str = "openai"
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    extra_body: Mapping[str, Any] = field(default_factory=dict)
    thinking: bool = True          # False: send the Qwen / vLLM thinking switches (openai provider only)
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: int = DEFAULT_TIMEOUT_S
    caller: str = DEFAULT_CALLER
    images_allowed: bool = False   # an image may only be sent when the operator said so for this endpoint

    @property
    def base(self) -> str:
        """The base URL without a trailing slash; the Anthropic default when none is set for that provider."""
        raw = self.base_url or (ANTHROPIC_BASE_URL if self.provider == "anthropic" else "")
        return raw.rstrip("/")

    @property
    def configured(self) -> bool:
        return bool(self.model and self.base)

    @property
    def url(self) -> str:
        if self.provider == "anthropic":
            return self.base + ("/messages" if self.base.endswith("/v1") else "/v1/messages")
        return self.base + "/chat/completions"

    @property
    def destination(self) -> Tuple[str, str, str, int, str]:
        """Where calls go, compared the way a URL is resolved: provider, scheme, lower-case host, port (the scheme's
        default when none is written) and path without a trailing slash."""
        u = urlsplit(self.base)
        scheme = (u.scheme or "").lower()
        port = u.port or {"https": 443, "http": 80}.get(scheme, 0)
        return self.provider, scheme, (u.hostname or "").lower(), port, u.path.rstrip("/")

    def secrets(self) -> List[str]:
        """Values that must never appear in a recorded error: the key, the value of every credential-like header (and
        the token after Bearer / Basic), and every string in the extra body under a credential-like key."""
        out = [self.api_key]
        for name, value in self.headers.items():
            out += _AUTH_SCHEME.findall(value)
            if _CREDENTIAL_NAME.search(name):
                out.append(value)
        out += _credential_strings(self.extra_body)
        return [v for v in out if v]


# A header or body key whose value is a credential (api-key, x-api-key, authorization, x-goog-api-key, *token*, ...).
_CREDENTIAL_NAME = re.compile(r"key|token|auth|secret|cookie|passw|pwd|sig|credential|jwt|session|bearer", re.I)
_AUTH_SCHEME = re.compile(r"^\s*(?:bearer|basic)\s+(\S+)", re.I)


def _credential_strings(data: Any, under_credential: bool = False) -> List[str]:
    """String values of a JSON value that sit under a credential-like key, at any depth."""
    if isinstance(data, Mapping):
        return [v for k, sub in data.items()
                for v in _credential_strings(sub, under_credential or bool(_CREDENTIAL_NAME.search(str(k))))]
    if isinstance(data, list):
        return [v for sub in data for v in _credential_strings(sub, under_credential)]
    return [data] if under_credential and isinstance(data, str) else []


def _json_object(name: str, raw: str) -> Dict[str, Any]:
    try:
        data = json.loads(raw)
    except ValueError:
        raise LLMConfigError(f"{name} is not valid JSON (it must be a JSON object)") from None
    if not isinstance(data, dict):
        raise LLMConfigError(f"{name} must be a JSON object")
    return data


# Headers that describe the connection or the body, not the call: setting them could desynchronise the request.
_TRANSPORT_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})


def _headers(name: str, raw: str) -> Dict[str, str]:
    data = _json_object(name, raw)
    if not all(isinstance(v, str) for v in data.values()):
        raise LLMConfigError(f"{name} must map header names to string values")
    if any(k.lower() in _TRANSPORT_HEADERS for k in data):
        raise LLMConfigError(f"{name} may not set {', '.join(sorted(_TRANSPORT_HEADERS))}")
    return {k: v.strip() for k, v in data.items()}


def _extra_body(name: str, raw: str) -> Dict[str, Any]:
    data = _json_object(name, raw)
    if {"model", "messages"} & set(data):
        raise LLMConfigError(f"{name} may not set model or messages (use the MODEL setting; the prompt is built here)")
    return data


def _base_url(name: str, raw: str) -> str:
    """An http(s) base URL with no credentials, query or fragment (a key belongs in the API_KEY or HEADERS setting)."""
    if any(c.isspace() or ord(c) < 32 for c in raw):
        raise LLMConfigError(f"{name} may not contain whitespace or control characters")
    try:
        parts = urlsplit(raw)
        parts.port                                  # raises on an out-of-range port
    except ValueError:
        raise LLMConfigError(f"{name} is not a valid URL") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise LLMConfigError(f"{name} must be an http:// or https:// URL")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise LLMConfigError(f"{name} may not carry credentials, a query or a fragment")
    return raw


def _provider(name: str, raw: str) -> str:
    if raw.lower() not in PROVIDERS:
        raise LLMConfigError(f"{name} must be one of {', '.join(PROVIDERS)}")
    return raw.lower()


def _int(name: str, raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise LLMConfigError(f"{name} must be an integer") from None
    if not 1 <= value <= 10 ** 6:
        raise LLMConfigError(f"{name} must be between 1 and 1000000")
    return value


def _text(name: str, raw: str) -> str:
    return raw


def _thinking(name: str, raw: str) -> bool:
    """True = send no switches. 'off' (or 0 / false / no) asks for reasoning to be switched off."""
    return raw.lower() not in ("off", "0", "false", "no")


def _flag(name: str, raw: str) -> bool:
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    raise LLMConfigError(f"{name} must be true or false")


# Endpoint field, the role variable's suffix (HEAL_<ROLE>_<suffix>), the shared variable (None: the role's own only), its
# parser, and whether it says WHERE calls go (a destination setting is inherited by the text role only).
_FIELDS = (
    ("provider", "PROVIDER", "HEAL_PROVIDER", _provider, True),
    ("base_url", "BASE_URL", "HEAL_BASE_URL", _base_url, True),
    ("model", "MODEL", "HEAL_MODEL", _text, True),
    ("api_key", "API_KEY", "HEAL_API_KEY", _text, True),
    ("headers", "HEADERS", "HEAL_HEADERS", _headers, True),
    ("extra_body", "EXTRA_BODY", "HEAL_EXTRA_BODY", _extra_body, True),
    ("images_allowed", "IMAGES_ALLOWED", None, _flag, False),
    ("thinking", "THINKING", "HEAL_THINKING", _thinking, False),
    ("max_tokens", "MAX_TOKENS", "HEAL_MAX_OUTPUT_TOKENS", _int, False),
    ("timeout", "TIMEOUT_S", "HEAL_TIMEOUT_S", _int, False),
)


def env_fields(env: Mapping[str, str], role: Optional[str] = None, destination: bool = True) -> Dict[str, Any]:
    """The Endpoint fields set in `env`: the shared HEAL_* variables, or (with `role`) only that role's HEAL_<ROLE>_*
    ones. `destination=False` leaves out the settings that say where calls go. Values are stripped (a trailing newline
    from an env file breaks a header); empty means unset."""
    out: Dict[str, Any] = {}
    for name, suffix, shared, parse, is_destination in _FIELDS:
        if role is None and (shared is None or (is_destination and not destination)):
            continue
        var = f"HEAL_{role.upper()}_{suffix}" if role else shared
        raw = (env.get(var) or "").strip()
        if raw:
            out[name] = parse(var, raw)
    return out


def for_role(shared: Endpoint, overrides: Mapping[str, Any]) -> Endpoint:
    """`shared` with a role's own settings on top. Credentials never follow a role to another destination: a role whose
    provider or base URL resolves elsewhere keeps the shared API key, headers and extra body only if it sets its own."""
    ep = replace(shared, **overrides)
    if ep.destination != shared.destination:
        ep = replace(ep, api_key=overrides.get("api_key", ""), headers=overrides.get("headers", {}),
                     extra_body=overrides.get("extra_body", {}))
    return ep


def endpoint_from_env(role: str, env: Optional[Mapping[str, str]] = None) -> Endpoint:
    """The endpoint for `role`: each HEAL_<ROLE>_* setting, else its shared fallback, else the default.

    Only the text role inherits the plain HEAL_BASE_URL / HEAL_MODEL / HEAL_API_KEY (and provider, headers, extra
    body): a vision call must be configured on purpose, so a vision endpoint with nothing set is "not configured", not
    "the text model with an image attached". Reasoning, max_tokens and the timeout are shared by every role."""
    env = os.environ if env is None else env
    if role not in ROLES:
        raise LLMConfigError(f"role must be one of {', '.join(ROLES)}")
    common: Dict[str, Any] = {"thinking": False, "max_tokens": DEFAULT_MAX_TOKENS, "timeout": DEFAULT_TIMEOUT_S}
    common.update(env_fields(env, destination=(role == "text")))
    caller = env["HEAL_CALLER"] if "HEAL_CALLER" in env else DEFAULT_CALLER
    shared = Endpoint(**common, caller=caller.strip())
    return for_role(shared, env_fields(env, role))


def _merge(body: Dict[str, Any], extra: Mapping[str, Any]) -> Dict[str, Any]:
    for k, v in extra.items():
        if v is None:
            body.pop(k, None)
        else:
            body[k] = v
    return body


def build_request(ep: Endpoint, prompt: str, images: Sequence[Image] = (),
                  heal_id: str = "") -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """(url, headers, json body) for one single-turn call: the prompt, then each image after its label.

    `heal_id` names the heal this call belongs to (X-Heal-Id); an id that is not plain id characters is left off rather
    than put into a header. Images are refused unless the endpoint was allowed to receive them."""
    if images and not ep.images_allowed:
        raise ImagesNotAllowed("this endpoint has not been allowed to receive images")
    prompt = prompt.encode("utf-8", "replace").decode("utf-8")   # a lone surrogate from page text cannot be sent as JSON
    headers = {"Content-Type": "application/json"}
    if ep.caller:
        headers["X-Heal-Caller"] = ep.caller
        headers["X-Caller"] = ep.caller               # gateways attribute cost by X-Caller
    if heal_id and _SAFE_ID.match(heal_id):
        headers["X-Heal-Id"] = heal_id
    if ep.provider == "anthropic":
        headers["anthropic-version"] = ANTHROPIC_VERSION
        if ep.api_key:
            headers["x-api-key"] = ep.api_key
        content: Any = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for label, png in images:
                content += [{"type": "text", "text": label},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                         "data": base64.b64encode(png).decode()}}]
        # no temperature (current models reject sampling parameters) and no thinking field (it cannot be disabled)
        body: Dict[str, Any] = {"model": ep.model, "max_tokens": ep.max_tokens,
                                "messages": [{"role": "user", "content": content}]}
    else:
        if ep.api_key:
            headers["Authorization"] = f"Bearer {ep.api_key}"
        body = {"model": ep.model, "temperature": 0, "max_tokens": ep.max_tokens}
        if not ep.thinking:
            prompt += NO_THINK
            body["chat_template_kwargs"] = {"enable_thinking": False}
            body["reasoning_effort"] = "none"
        content = prompt
        if images:
            content = [{"type": "text", "text": prompt}]
            for label, png in images:
                content += [{"type": "text", "text": label},
                            {"type": "image_url", "image_url": {"url": "data:image/png;base64,"
                                                                       + base64.b64encode(png).decode()}}]
        body["messages"] = [{"role": "user", "content": content}]
    for name, value in ep.headers.items():          # an override replaces the default whatever its case
        for existing in [k for k in headers if k.lower() == name.lower()]:
            del headers[existing]
        headers[name] = value
    return ep.url, headers, _merge(body, ep.extra_body)


@dataclass
class Reply:
    """The answer text (None when there is none) and, when there is none, why."""
    text: Optional[str]
    why: str = ""


def parse_reply(ep: Endpoint, data: Any) -> Reply:
    """The answer text of a response body. Reasoning fields and thinking blocks are never read."""
    try:
        if ep.provider == "anthropic":
            if data.get("stop_reason") == "refusal":
                return Reply(None, "the model refused")
            parts = [b.get("text", "") for b in data["content"] if isinstance(b, dict) and b.get("type") == "text"]
            return Reply("".join(parts) if parts else None, "" if parts else "no text block in the response")
        content = data["choices"][0]["message"].get("content")
    except (KeyError, IndexError, TypeError, AttributeError):
        return Reply(None, "response has no choices[0].message" if ep.provider == "openai" else "response has no content")
    return Reply(content if isinstance(content, str) else None, "")


@dataclass(frozen=True)
class Usage:
    """Token counts of one call as integers; a count the provider did not report is 0."""
    input_tokens: int = 0
    output_tokens: int = 0
    image_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.image_tokens + other.image_tokens)

    def as_dict(self) -> Dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "image_tokens": self.image_tokens}


def _count(value: Any) -> int:
    """A reported token count: a non-negative int (a bool is not one), else 0."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def usage_of(ep: Endpoint, data: Any) -> Usage:
    """Token counts of a response body, whichever names the provider uses (OpenAI prompt_tokens / completion_tokens,
    Anthropic input_tokens / output_tokens), as one vocabulary of integers."""
    usage = data.get("usage") if isinstance(data, dict) else None
    usage = usage if isinstance(usage, dict) else {}
    if ep.provider == "anthropic":
        return Usage(_count(usage.get("input_tokens")), _count(usage.get("output_tokens")))
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    mm = details.get("multimodal_tokens") if isinstance(details.get("multimodal_tokens"), dict) else {}
    return Usage(_count(usage.get("prompt_tokens")), _count(usage.get("completion_tokens")), _count(mm.get("image")))
