"""
===============================================================================
Configuration Readers (environment-driven, parsed once, one message per mistake)
===============================================================================
The small readers every setting goes through, so each environment variable is parsed in one place with one default and
one error message. A setting is declared as a field name, the variable(s) it can come from (the first that is set wins)
and a parser; `_parse` turns an environment mapping into the fields it sets and leaves the rest out, so a default is
written once, in the settings dataclass that uses these readers (ServiceSettings, below). The model endpoints have their
own readers in llm.py; ServiceSettings holds everything else the service is told: the token, the audit store, the
retention, the token budget and the price per model.

Features:
    ✓ An invalid number, yes/no value or choice raises ConfigError naming the variable and what it accepts, never the
      value (it may be a secret pasted into the wrong variable)
    ✓ One boolean parser: 1/true/yes/on and 0/false/no/off, any case
    ✓ A blank variable is unset (the default applies) unless the setting says an empty value means something
    ✓ Several variable names per setting, in priority order (a new name first, the old one as a fallback)
    ✓ Pure: `_parse` reads the mapping it is given, never the process environment on its own

Environment Variables:
    HEAL_SERVICE_TOKEN       bearer token clients must send (unset = no auth, loopback bind only)
    HEAL_DB                  path of the SQLite audit store (default .heal/voyageur.db)
    HEAL_RETENTION_DAYS      days a heal record is kept (default 14; 0 keeps nothing)
    HEAL_RUN_TOKEN_BUDGET    model tokens one run may spend before the rest of its failures go to a human (unset = none)
    HEAL_CANDIDATE_CAP       lines in the pick list (default 30)
    HEAL_MAX_CALLS           model calls per heal (default 2)
    HEAL_OPEN_TTL_S          seconds an unanswered conversation is kept before it is closed (default 900)
    HEAL_LIBRARY_CLASSES     component libraries whose class names may be written as css locators, from mui, ant, chakra, mantine, mat
                             (comma separated, any case, an optional trailing dash; default empty = off)
    HEAL_AUDIT_BUDGET_S      seconds of work one POST /v1/audit may spend before it answers with the rows done (default 5)
    HEAL_MODEL_PRICES        JSON {"model": {"input": n, "output": n}}, price per million tokens, for cost figures
    HEAL_DIAGNOSE            on turns POST /v1/diagnose and the MCP tool diagnose_failure on (default off: the error text, aria
                             snapshot and expect() literals they send to the model are an opt-in)

Author: PMAC
===============================================================================
"""
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Tuple


class ConfigError(ValueError):
    """An environment variable holds a value the service cannot use. The message names the variable and what it
    accepts."""


Parser = Callable[[str, str], Any]


def _text(name: str, raw: str) -> str:
    return raw


def _integer(minimum: int) -> Parser:
    def parse(name: str, raw: str) -> int:
        try:
            value = int(raw)
        except ValueError:
            value = minimum - 1
        if value < minimum:
            raise ConfigError(f"{name} must be a whole number of at least {minimum}")
        return value
    return parse


def _seconds(minimum: float) -> Parser:
    def parse(name: str, raw: str) -> float:
        try:
            value = float(raw)
        except ValueError:
            value = minimum - 1
        if not value >= minimum or value == float("inf"):
            raise ConfigError(f"{name} must be a number of seconds of at least {minimum:g}")
        return value
    return parse


_TRUE, _FALSE = ("1", "true", "yes", "on"), ("0", "false", "no", "off")


def _boolean(name: str, raw: str) -> bool:
    word = raw.lower()
    if word not in _TRUE + _FALSE:
        raise ConfigError(f"{name} must be one of {' | '.join(_TRUE + _FALSE)}")
    return word in _TRUE


def _choice(*allowed: str, aliases: Optional[Mapping[str, str]] = None) -> Parser:
    def parse(name: str, raw: str) -> str:
        word = raw.lower()
        word = (aliases or {}).get(word, word)
        if word not in allowed:
            raise ConfigError(f"{name} must be one of {' | '.join(allowed)}")
        return word
    return parse


@dataclass(frozen=True)
class _Variable:
    """One settings field, the variable(s) it comes from (the first that is set wins) and how to parse it."""
    field: str
    names: Tuple[str, ...]
    parse: Parser
    keep_empty: bool = False     # a set-but-empty variable is a value (an empty caller means "send no header")


def _v(field_name: str, names: str, parse: Parser = _text, keep_empty: bool = False) -> _Variable:
    return _Variable(field_name, tuple(names.split()), parse, keep_empty)


def _parse(env: Mapping[str, str], variables: Tuple[_Variable, ...]) -> Dict[str, Any]:
    """The fields `env` sets, parsed. An unset (or blank) variable is absent from the result, so the dataclass default
    applies; that is how a default is written only once."""
    out: Dict[str, Any] = {}
    for var in variables:
        for name in var.names:
            raw = env.get(name)
            if raw is None or (not raw.strip() and not var.keep_empty):
                continue
            out[var.field] = var.parse(name, raw.strip())
            break
    return out


def _libraries(name: str, raw: str) -> Tuple[str, ...]:
    """HEAL_LIBRARY_CLASSES as a tuple of known library names; an unknown one is a ConfigError that lists the known set."""
    from .locator_policy import LIBRARY_PREFIXES
    out = []
    for part in raw.split(","):
        word = part.strip().lower().rstrip("-")
        if not word:
            continue
        if word not in LIBRARY_PREFIXES:
            raise ConfigError(f"{name} must list libraries from {', '.join(LIBRARY_PREFIXES)}")
        if word not in out:
            out.append(word)
    return tuple(out)


def _prices(name: str, raw: str) -> Dict[str, Tuple[float, float]]:
    """{"model": {"input": n, "output": n}} as {model: (input, output)} prices per million tokens."""
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    out: Dict[str, Tuple[float, float]] = {}
    if isinstance(data, dict):
        for model, price in data.items():
            ok = isinstance(price, dict) and all(isinstance(price.get(k), (int, float)) and not isinstance(price.get(k), bool)
                                                 and price[k] >= 0 for k in ("input", "output"))
            if not ok or not isinstance(model, str):
                data = None
                break
            out[model] = (float(price["input"]), float(price["output"]))
    if not isinstance(data, dict):
        raise ConfigError(f'{name} must be a JSON object like {{"model": {{"input": 1.0, "output": 2.0}}}}')
    return out


_SETTINGS = (
    _v("token", "HEAL_SERVICE_TOKEN"),
    _v("db_path", "HEAL_DB"),
    _v("retention_days", "HEAL_RETENTION_DAYS", _integer(0)),
    _v("run_token_budget", "HEAL_RUN_TOKEN_BUDGET", _integer(1)),
    _v("candidate_cap", "HEAL_CANDIDATE_CAP", _integer(1)),
    _v("max_calls", "HEAL_MAX_CALLS", _integer(1)),
    _v("open_ttl_s", "HEAL_OPEN_TTL_S", _integer(1)),
    _v("request_deadline_s", "HEAL_REQUEST_DEADLINE_S", _seconds(0.001)),
    _v("workers", "HEAL_WORKERS", _integer(0)),
    _v("audit_budget_s", "HEAL_AUDIT_BUDGET_S", _seconds(0.1)),
    _v("library_classes", "HEAL_LIBRARY_CLASSES", _libraries),
    _v("prices", "HEAL_MODEL_PRICES", _prices),
    _v("diagnose_enabled", "HEAL_DIAGNOSE", _boolean),
)


@dataclass(frozen=True)
class ServiceSettings:
    """What the service is told, other than model endpoints (llm.endpoint_from_env)."""
    token: Optional[str] = None
    db_path: str = ".heal/voyageur.db"
    retention_days: int = 14
    run_token_budget: Optional[int] = None
    candidate_cap: int = 30
    max_calls: int = 2
    open_ttl_s: int = 900             # an open conversation the client has not answered in this long is closed
    request_deadline_s: float = 10.0  # wall-clock seconds one request may spend reading client text (the model call excluded)
    workers: int = 2                  # worker processes that run that work so it can be stopped; 0 runs it in-process
    audit_budget_s: float = 5.0       # work one audit may spend; past it the answer is the rows done, with truncated true
    library_classes: Tuple[str, ...] = ()   # component libraries whose class names may be written (locator_policy reads the same variable)
    prices: Mapping[str, Tuple[float, float]] = field(default_factory=dict)
    diagnose_enabled: bool = False    # POST /v1/diagnose answers only when on: it sends failure text to a model, which no heal does

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "ServiceSettings":
        """The settings `env` holds; ConfigError names the variable that cannot be used."""
        return cls(**_parse(env, _SETTINGS))
