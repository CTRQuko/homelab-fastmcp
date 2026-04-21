"""Secure secrets loader for native tools.

Never hardcode secrets. Load from (in priority order):
1. Environment variables
2. C:/homelab/.config/secrets/*.md files
3. .env file in project root (fallback)

Secrets are masked in error messages.
"""
import os
import warnings
from pathlib import Path

# ---------------------------------------------------------------------------
# Search paths (priority order)
# ---------------------------------------------------------------------------

_HOMELAB_DIR = os.environ.get("HOMELAB_DIR", "C:/homelab")
_SECRET_DIRS = [
    Path(_HOMELAB_DIR) / ".config/secrets",
]

_PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _from_env(key: str) -> str | None:
    """Load from environment variable."""
    val = os.environ.get(key, "").strip()
    return val if val else None


def _from_md_files(key: str) -> str | None:
    """Load from C:/homelab/.config/secrets/*.md files.

    Format per file:
        KEY_NAME=value_here

    Si detecta la misma clave definida más de una vez (en un fichero o entre
    varios), emite UserWarning y devuelve la PRIMERA coincidencia.
    """
    found: str | None = None
    duplicates_found = 0
    for directory in _SECRET_DIRS:
        if not directory.exists():
            continue
        for file in directory.glob("*.md"):
            try:
                for line in file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith(f"{key}="):
                        value = line.split("=", 1)[1].strip()
                        if found is None:
                            found = value
                        elif value != found:
                            duplicates_found += 1
            except OSError:
                continue
    if duplicates_found > 0:
        warnings.warn(
            f"Secret '{key}' definido {duplicates_found + 1} veces en secrets/*.md "
            f"con valores distintos; usando la primera coincidencia.",
            UserWarning,
            stacklevel=3,
        )
    return found


def _from_dotenv(key: str) -> str | None:
    """Load from .env file in project root."""
    if not _PROJECT_ENV.exists():
        return None
    try:
        for line in _PROJECT_ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load(key: str) -> str:
    """Load a secret securely.

    Priority:
        1. Environment variable
        2. secrets/*.md files
        3. .env in project root

    Raises:
        RuntimeError: if secret not found anywhere, with masked hints.
    """
    val = _from_env(key)
    if val:
        return val

    val = _from_md_files(key)
    if val:
        return val

    val = _from_dotenv(key)
    if val:
        return val

    # Build helpful error without exposing anything
    sources = []
    if any(d.exists() for d in _SECRET_DIRS):
        sources.append("secrets/*.md")
    if _PROJECT_ENV.exists():
        sources.append(".env")
    sources.append("environment variable")

    raise RuntimeError(
        f"Secret '{key}' not found. "
        f"Set it as: {', '.join(sources)}"
    )


def load_optional(key: str, default: str = "") -> str:
    """Load a secret, return default if not found."""
    try:
        return load(key)
    except RuntimeError:
        return default


def mask(value: str, visible: int = 4) -> str:
    """Mask a secret for safe logging.

    Example: mask("abcd1234") -> "abcd****"
    """
    if not value:
        return "<empty>"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return value[:visible] + "****"
