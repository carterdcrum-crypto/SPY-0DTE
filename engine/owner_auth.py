"""Single-owner password authentication. No Google login or public preview access."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException

SESSION_SECONDS = 12 * 60 * 60
SCRYPT_N = 1 << 14


@dataclass(frozen=True)
class Owner:
    username: str


def _db_path() -> Path:
    return Path(os.environ.get("OWNER_AUTH_DB_PATH", "/data/spy_owner_auth.sqlite"))


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=12)
    conn.execute("PRAGMA busy_timeout=12000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS owner (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        username TEXT UNIQUE NOT NULL COLLATE NOCASE,
        salt BLOB NOT NULL, password_hash BLOB NOT NULL,
        created_at REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
        token_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS failures (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        count INTEGER NOT NULL, locked_until REAL NOT NULL
    )""")
    conn.commit()
    return conn


def _digest(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N,
                          r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)


def _validate(username: str, password: str) -> str:
    name = username.strip()
    if not (3 <= len(name) <= 32 and all(c.isascii() and (c.isalnum() or c in "._-") for c in name)):
        raise HTTPException(422, "Username must be 3–32 letters, digits, dots, dashes, or underscores.")
    if not (14 <= len(password) <= 256):
        raise HTTPException(422, "Password must be 14–256 characters.")
    return name


def _issue(conn: sqlite3.Connection) -> str:
    # Exactly one active session for the sole owner. A new sign-in revokes
    # every prior phone/session without any separate device-management API.
    conn.execute("DELETE FROM sessions")
    token = secrets.token_urlsafe(48)
    conn.execute("INSERT INTO sessions(token_hash,expires_at) VALUES(?,?)",
                 (hashlib.sha256(token.encode("ascii")).hexdigest(), time.time() + SESSION_SECONDS))
    return token


def signup_available() -> dict:
    with _connect() as conn:
        claimed = conn.execute("SELECT 1 FROM owner WHERE singleton=1").fetchone() is not None
    return {"signup_available": not claimed and bool(os.environ.get("OWNER_SETUP_CODE","").strip()),
            "owner_created": claimed}


def signup(username: str, password: str, setup_code: str) -> str:
    name = _validate(username, password)
    expected = os.environ.get("OWNER_SETUP_CODE", "").strip()
    if len(expected) < 32:
        raise HTTPException(503, "Owner enrollment code is not configured on Railway.")
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("SELECT 1 FROM owner WHERE singleton=1").fetchone():
            raise HTTPException(409, "Owner already exists. Sign in instead.")
        if not hmac.compare_digest(setup_code.encode("utf-8"), expected.encode("utf-8")):
            raise HTTPException(403, "Invalid one-time owner setup code.")
        salt = secrets.token_bytes(16)
        conn.execute("INSERT INTO owner(singleton,username,salt,password_hash,created_at) VALUES(1,?,?,?,?)",
                     (name, salt, _digest(password, salt), time.time()))
        token = _issue(conn)
        conn.commit()
        return token


def login(username: str, password: str) -> str:
    name = username.strip()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        now = time.time()
        fail = conn.execute("SELECT count,locked_until FROM failures WHERE singleton=1").fetchone()
        if fail and fail[1] > now:
            raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
        record = conn.execute("SELECT username,salt,password_hash FROM owner WHERE singleton=1").fetchone()
        # Equal-cost comparison even when username does not exist.
        salt = record[1] if record else b"0" * 16
        stored = record[2] if record else b"0" * 32
        match = hmac.compare_digest(_digest(password, salt), stored)
        if not (record and hmac.compare_digest(name.casefold(), record[0].casefold()) and match):
            count = (fail[0] if fail and fail[1] <= now else 0) + 1
            conn.execute("INSERT OR REPLACE INTO failures(singleton,count,locked_until) VALUES(1,?,?)",
                         (count if count < 5 else 0, now + 900 if count >= 5 else 0))
            conn.commit()
            raise HTTPException(401, "Invalid owner credentials.")
        conn.execute("DELETE FROM failures")
        conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))
        token = _issue(conn)
        conn.commit()
        return token


def verify(token: str) -> Owner:
    if not token or len(token) > 256:
        raise HTTPException(401, "Invalid or expired owner session.")
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        row = conn.execute("SELECT owner.username FROM sessions JOIN owner ON owner.singleton=1 "
                           "WHERE sessions.token_hash=? AND sessions.expires_at>?",
                           (digest, time.time())).fetchone()
    if not row:
        raise HTTPException(401, "Invalid or expired owner session.")
    return Owner(username=row[0])


def logout(token: str) -> None:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
        conn.commit()
