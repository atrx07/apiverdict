"""Config + auth resolution for apiverdict (v2).

Config file format (JSON; TOML is also accepted on Python 3.11+ via
tomllib — on 3.10 a TOML file is a clear error, not a silent guess):

    {
      "default_environment": "local",
      "environments": {
        "local": {
          "base_url": "http://127.0.0.1:8765",
          "auth": {"type": "bearer", "env_var": "APIVERDICT_TOKEN"}
        },
        "staging": {
          "base_url": "https://staging.example.invalid",
          "auth": {"type": "api-key", "env_var": "APIVERDICT_API_KEY",
                     "header": "X-Api-Key"}
        }
      }
    }

Auth rules (deliberately strict, because secrets are the failure mode):
  * Env-var names are explicit in the config. apiverdict never auto-scrapes
    the environment for plausible-looking token variables.
  * A missing env var is an error (exit 2) at setup time, never an empty
    header silently sent (an empty Authorization header can look like "anonymous"
    and give a false PASS on an auth-matrix check).
  * Secret VALUES are never put into a Verdict evidence dict, a report, or
    a log line. Evidence may name the env var, never its value.
    `redact_headers()` is the only safe way to display headers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    tomllib = None  # type: ignore

REDACTED = "[redacted]"
_SENSITIVE_HEADERS = {"authorization", "x-api-key", "api-key", "x-auth-token"}


def load_config(path: str | Path) -> dict:
    """Load a JSON (or, on 3.11+, TOML) config file. Raises ValueError."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"config file not found: {path}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".toml":
        if tomllib is None:
            raise ValueError(
                "TOML config requires Python 3.11+ (tomllib); "
                "use a JSON config file on Python 3.10, or convert the "
                "TOML file to JSON first"
            )
        try:
            return tomllib.loads(text)
        except Exception as exc:
            raise ValueError(f"invalid TOML config {path}: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"invalid JSON config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"config {path} must be a JSON object, not {type(data).__name__}")
    return data


def get_environment(config: dict, name: str | None = None) -> dict:
    """Pick a named environment, or the config's default, or 'local'."""
    envs = config.get("environments") or {}
    if not isinstance(envs, dict) or not envs:
        # A flat config with just base_url/auth is one implicit environment.
        if config.get("base_url"):
            return config
        raise ValueError("config has no 'environments' section and no base_url")
    if name is None:
        name = config.get("default_environment") or "local"
    if name not in envs:
        raise ValueError(
            f"unknown environment {name!r}; available: {sorted(envs)}"
        )
    env = envs[name]
    if not isinstance(env, dict):
        raise ValueError(f"environment {name!r} must be an object")
    return env


def resolve_auth(auth: dict | None) -> dict[str, str]:
    """Turn an auth spec into headers. Raises ValueError if the secret
    env var is missing. Returns {} for no auth. Never logs the value."""
    if not auth:
        return {}
    auth_type = str(auth.get("type", "bearer")).lower()
    env_var = auth.get("env_var")
    if not env_var:
        raise ValueError("auth spec must name an 'env_var' (no auto-scraping)")
    value = os.environ.get(env_var)
    if not value:
        raise ValueError(
            f"auth env var {env_var!r} is not set; refusing to send an "
            "empty credential (that would masquerade as anonymous access)"
        )
    if auth_type == "bearer":
        return {"Authorization": f"Bearer {value}"}
    if auth_type == "api-key":
        header = auth.get("header", "X-Api-Key")
        return {header: value}
    if auth_type == "basic":
        # Value is expected to be the already-encoded "user:pass" pair's
        # base64, or a raw "user:pass" — we do not invent encoding rules.
        import base64

        if ":" in value and " " not in value:
            # Heuristic: looks like user:pass, encode it.
            value = base64.b64encode(value.encode()).decode()
        return {"Authorization": f"Basic {value}"}
    raise ValueError(f"unsupported auth type {auth_type!r} (bearer, api-key, basic)")


def resolve_headers(env: dict, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Config auth headers, with explicit --header values winning on clash."""
    headers = resolve_auth(env.get("auth"))
    for key, val in (extra or {}).items():
        headers[key] = val
    return headers


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Safe-for-display copy: sensitive header values replaced."""
    out = {}
    for key, val in headers.items():
        if key.lower() in _SENSITIVE_HEADERS or "token" in key.lower():
            out[key] = REDACTED
        else:
            out[key] = val
    return out


def auth_evidence(env: dict) -> dict:
    """Evidence-safe auth summary: names only, never values."""
    auth = env.get("auth") or {}
    if not auth:
        return {"auth": "none"}
    return {
        "auth": str(auth.get("type", "bearer")),
        "auth_env_var": str(auth.get("env_var", "")),
        "auth_header": str(auth.get("header", "Authorization")),
    }
