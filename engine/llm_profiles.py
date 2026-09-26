"""Named LLM connection profiles (方案) stored in ``config/llm_profiles.yaml``.

A profile is one OpenAI-compatible endpoint: ``name`` + ``base_url`` +
``api_key`` + ``model``. The store holds several profiles plus which one is
active, so users can keep "DeepSeek", "OpenAI", "公司网关" side by side and
switch with one click instead of overwriting the single config file.

The file holds plaintext API keys, so it is gitignored. The legacy flat
``config/api_config.yaml`` layout (``api_key`` / ``base_url`` / ``model``) is
still readable as a fallback profile, so existing installations and the CLI
``/set-key`` flow keep working.

This module is deliberately free of domain knowledge: it knows nothing about
thesis / official-document rules, only about LLM endpoints.
"""

from __future__ import annotations

import os

import yaml

from engine.paths import resolve

PROFILE_FILE = "llm_profiles.yaml"
LEGACY_FILE = "api_config.yaml"

#: Name given to the profile synthesised from the legacy flat config file.
LEGACY_PROFILE_NAME = "默认（api_config.yaml）"

#: Keys every profile carries, in canonical order.
FIELDS = ("name", "base_url", "api_key", "model")


def profile_store_path() -> str:
    """Absolute path of the profile store (lazy: see ``api_config.yaml``)."""
    return resolve("config", PROFILE_FILE)


def legacy_config_path() -> str:
    """Absolute path of the legacy flat LLM config file."""
    return resolve("config", LEGACY_FILE)


def _read_yaml(path: str) -> dict:
    """Read a YAML mapping; return ``{}`` if missing, empty or unreadable."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def normalize_profile(raw) -> dict:
    """Coerce ``raw`` into a canonical profile dict with all four keys.

    Missing keys become empty strings so callers can diff / display them
    without guarding every access. Input is never mutated.
    """
    if not isinstance(raw, dict):
        raw = {}
    out = {k: (raw.get(k) or "") for k in FIELDS}
    out["name"] = str(out["name"]).strip() or "未命名"
    for k in ("base_url", "api_key", "model"):
        out[k] = str(out[k]).strip()
    return out


def _save_store(store: dict) -> None:
    """Write the whole store atomically enough for our purposes."""
    path = profile_store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = {
        "active": store.get("active") or "",
        "profiles": store.get("profiles") or [],
    }
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(ordered, fh, allow_unicode=True, default_flow_style=False)


def _read_store() -> dict:
    """Return ``{"active": str, "profiles": [profile, ...]}`` from disk."""
    raw = _read_yaml(profile_store_path())
    profiles = raw.get("profiles")
    if not isinstance(profiles, list):
        profiles = []
    clean = []
    for item in profiles:
        if isinstance(item, dict):
            clean.append(normalize_profile(item))
    return {"active": str(raw.get("active") or "").strip(), "profiles": clean}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_profiles() -> list:
    """All stored profiles, in file order, normalised."""
    return list(_read_store().get("profiles") or [])


def profile_names() -> list:
    """Names of all stored profiles, in file order."""
    return [p["name"] for p in _read_store().get("profiles") or []]


def get_profile(name: str):
    """Return the stored profile called ``name``, or ``None``."""
    if not name:
        return None
    for p in _read_store().get("profiles") or []:
        if p["name"] == name:
            return p
    return None


def legacy_profile():
    """Synthesise a profile from the legacy flat ``api_config.yaml``, or ``None``.

    ``None`` when the file is absent or carries no ``api_key``. The legacy file
    is never rewritten by the profile store.
    """
    raw = _read_yaml(legacy_config_path())
    key = str(raw.get("api_key") or "").strip()
    if not key:
        return None
    return normalize_profile({
        "name": LEGACY_PROFILE_NAME,
        "base_url": raw.get("base_url") or "",
        "api_key": key,
        "model": raw.get("model") or "",
    })


def active_name() -> str:
    """Name of the active profile, or ``""`` when nothing is set.

    Falls back to the first stored profile so a store whose ``active`` value
    is missing or stale (deleted profile) still yields a usable connection.
    """
    store = _read_store()
    names = [p["name"] for p in store.get("profiles") or []]
    active = store.get("active") or ""
    if active in names:
        return active
    return names[0] if names else ""


def active_profile():
    """The profile ``LLMClient()`` should use: store first, legacy second.

    Order matters: once a user has stored profiles, they own the truth. The
    legacy flat file is only consulted when the store is empty, which keeps
    pre-existing installations working without a migration step.
    """
    store = _read_store()
    if store.get("profiles"):
        name = active_name()
        profile = get_profile(name)
        if profile:
            return profile
    return legacy_profile()


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def upsert_profile(profile, old_name=None) -> dict:
    """Create or replace a profile by name; return the stored profile.

    Pass ``old_name`` when the working copy came from an already-saved profile
    whose name is being edited. Without it, saving under a new name would leave
    the old entry behind and the store would hold two copies of one endpoint.

    Editing an existing profile keeps its position in the list (only brand-new
    profiles are appended), so a dialog bound to this list doesn't jump around
    every time the user saves. ``active`` also stays pointed at something real:
    when the renamed profile was the active one, the pointer follows.
    """
    if not isinstance(profile, dict):
        raise ValueError("profile must be a dict")
    if not str(profile.get("name") or "").strip():
        raise ValueError("profile name must not be empty")
    out = normalize_profile(profile)
    old_name = str(old_name or "").strip()
    store = _read_store()
    existing = store.get("profiles") or []
    drop = {out["name"]}
    if old_name and old_name != out["name"]:
        drop.add(old_name)

    # Position of the surviving entry to keep, if the save is an edit.
    keep_at = None
    if old_name in [p["name"] for p in existing]:
        keep_at = next(i for i, p in enumerate(existing) if p["name"] == old_name)
    elif out["name"] in [p["name"] for p in existing]:
        keep_at = next(i for i, p in enumerate(existing) if p["name"] == out["name"])

    profiles = [p for p in existing if p["name"] not in drop]
    if keep_at is None:
        profiles.append(out)
    else:
        profiles.insert(min(keep_at, len(profiles)), out)
    store["profiles"] = profiles
    if store.get("active") and store["active"] not in [p["name"] for p in profiles]:
        store["active"] = out["name"]
    _save_store(store)
    return out


def delete_profile(name: str) -> bool:
    """Delete a profile by name. Return ``True`` if something was removed."""
    store = _read_store()
    profiles = [p for p in store.get("profiles") or [] if p["name"] != name]
    if len(profiles) == len(store.get("profiles") or []):
        return False
    store["profiles"] = profiles
    if store.get("active") == name:
        store["active"] = profiles[0]["name"] if profiles else ""
    _save_store(store)
    return True


def set_active(name: str) -> bool:
    """Mark ``name`` as the active profile. Return ``True`` on success."""
    store = _read_store()
    names = [p["name"] for p in store.get("profiles") or []]
    if name not in names:
        return False
    store["active"] = name
    _save_store(store)
    return True


def clear_store() -> None:
    """Drop the whole store. Used by tests."""
    path = profile_store_path()
    if os.path.exists(path):
        os.remove(path)
