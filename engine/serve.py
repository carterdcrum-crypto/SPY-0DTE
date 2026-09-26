from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from cryptography.fernet import Fernet


def _ensure_encryption_key() -> None:
    """Load or create the backend credential-encryption key without committing it."""
    if os.environ.get("APP_ENCRYPTION_KEY", "").strip():
        return

    key_path = Path(os.environ.get("APP_ENCRYPTION_KEY_PATH", "/data/app_encryption.key"))
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        key = key_path.read_text(encoding="utf-8").strip()
    else:
        key = Fernet.generate_key().decode("ascii")
        temp_path = key_path.with_suffix(key_path.suffix + ".tmp")
        temp_path.write_text(key + "\n", encoding="utf-8")
        os.chmod(temp_path, 0o600)
        temp_path.replace(key_path)
        os.chmod(key_path, 0o600)

    Fernet(key.encode("ascii"))
    os.environ["APP_ENCRYPTION_KEY"] = key


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    _ensure_encryption_key()
    port = int(os.environ.get("PORT", "8080"))
    app_module = (
        "engine.preview_api:app"
        if _env_bool("APP_PREVIEW_MODE", False)
        else "engine.webull_live_api:app"
    )
    uvicorn.run(app_module, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
