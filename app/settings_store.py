"""Encrypted local secret storage.

Secrets entered in the Settings screen are encrypted before they are written
to the database:
  * Windows: Windows Data Protection API (DPAPI) — tied to your Windows login,
    no extra packages needed (works on Windows-on-ARM).
  * Elsewhere: Fernet (from the `cryptography` package) with a key kept in
    `data/.secret.key` (permission 600).
Environment variables always take precedence. Secret values are never
returned to the browser — only whether they are set.
"""
from __future__ import annotations

import base64
import os
from pathlib import Path

from . import config, db

_KEY_FILE: Path = config.DATA_DIR / ".secret.key"


# ------------------------------------------------------------------ DPAPI ----
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    inp = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(inp), ctypes.c_wchar_p("BhashaRadar"), None, None, None,
                                      CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None,
                                        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise OSError("Windows could not encrypt/decrypt the secret")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)


# ----------------------------------------------------------------- Fernet ----
def _fernet():
    from cryptography.fernet import Fernet

    env_key = os.getenv("BR_SECRET_KEY")
    if env_key:
        try:
            return Fernet(env_key.encode())
        except ValueError:  # any random string works: derive a proper key from it
            import hashlib

            return Fernet(base64.urlsafe_b64encode(hashlib.sha256(env_key.encode()).digest()))
    key_file = Path(os.getenv("BR_SECRET_KEY_FILE", _KEY_FILE))
    if not key_file.exists():
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(Fernet.generate_key())
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
    return Fernet(key_file.read_bytes().strip())


def _encrypt(value: str) -> str:
    if os.name == "nt" and not os.getenv("BR_SECRET_KEY"):
        return "dpapi:" + base64.b64encode(_dpapi(value.encode(), True)).decode()
    try:
        return "fernet:" + _fernet().encrypt(value.encode()).decode()
    except ImportError as exc:
        raise RuntimeError("Install the 'cryptography' package to store secrets on this system, or put them in .env") from exc


def _decrypt(token: str) -> str | None:
    try:
        if token.startswith("dpapi:"):
            return _dpapi(base64.b64decode(token[6:]), False).decode()
        raw = token[7:] if token.startswith("fernet:") else token
        return _fernet().decrypt(raw.encode()).decode()
    except Exception:
        return None


# -------------------------------------------------------------------- API ----
def set_secret(name: str, value: str) -> None:
    if name not in config.ENV_KEYS and not name.startswith("GMAIL_TOKEN"):
        raise ValueError(f"Unknown secret name: {name}")
    if value == "":
        db.execute("DELETE FROM secrets WHERE name=?", (name,))
        return
    db.execute(
        "INSERT INTO secrets(name,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (name, _encrypt(value), db.now_iso()),
    )


def get_secret_value(name: str) -> str | None:
    row = db.query_one("SELECT value FROM secrets WHERE name=?", (name,))
    return _decrypt(row["value"]) if row else None


def status() -> dict[str, dict]:
    """Which keys are configured (never the values)."""
    out = {}
    for name, desc in config.ENV_KEYS.items():
        in_env = bool(os.getenv(name))
        in_store = db.query_one("SELECT 1 FROM secrets WHERE name=?", (name,)) is not None
        out[name] = {"description": desc, "set": in_env or in_store, "from_env": in_env}
    return out
