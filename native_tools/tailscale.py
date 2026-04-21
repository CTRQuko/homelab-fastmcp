"""Tailscale tools — nativas en Python con requests."""
import re

import requests

from .secrets import load as _load_secret

_BASE = "https://api.tailscale.com/api/v2"

# Tailscale device IDs are alphanumeric with hyphens/underscores.
# Límite de longitud (64): defensa contra payloads excesivos / DoS.
# Los IDs reales de Tailscale son nodeId cortos (<32 chars).
_DEVICE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _get_api_key() -> str:
    key = _load_secret("TAILSCALE_API_KEY")
    if not key:
        raise RuntimeError(
            "TAILSCALE_API_KEY no definida. "
            "Configúrala como variable de entorno, en secrets/*.md o en .env"
        )
    return key


def _get_tailnet() -> str:
    tailnet = _load_secret("TAILSCALE_TAILNET")
    if not tailnet:
        raise RuntimeError(
            "TAILSCALE_TAILNET no definida. "
            "Configúrala como variable de entorno, en secrets/*.md o en .env"
        )
    return tailnet


def _headers():
    return {"Authorization": f"Bearer {_get_api_key()}"}


def _sanitize_error(err: str) -> str:
    lowered = err.lower()
    patterns = [
        r"tskey-api-[^\s]+",
        r"tskey-oauth-[^\s]+",
        r"tskey-client-[^\s]+",
        r"api_key[^\s]*",
        r"apikey[^\s]*",
        r"api-secret[^\s]*",
        r"bearer\s+[^\s]+",
        r"token=[^\s]+",
        r"authorization:\s*[^\s]+",
    ]
    for p in patterns:
        if re.search(p, lowered):
            return "tailscale API error (credenciales ocultas)"
    return err


def _validate_device_id(device_id: str) -> None:
    if not device_id or not _DEVICE_ID_RE.match(device_id):
        raise ValueError("device_id inválido")


def list_devices() -> list[dict]:
    """Lista todos los dispositivos del tailnet."""
    url = f"{_BASE}/tailnet/{_get_tailnet()}/devices"
    try:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        return r.json().get("devices", [])
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error(str(e)))


def get_device(device_id: str) -> dict:
    """Detalles de un dispositivo por ID."""
    _validate_device_id(device_id)
    url = f"{_BASE}/device/{device_id}"
    try:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error(str(e)))


def get_acls() -> dict:
    """Política ACL actual (HuJSON)."""
    url = f"{_BASE}/tailnet/{_get_tailnet()}/acl"
    try:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error(str(e)))


def get_dns() -> dict:
    """Configuración DNS del tailnet."""
    url = f"{_BASE}/tailnet/{_get_tailnet()}/dns/preferences"
    try:
        r = requests.get(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error(str(e)))


def authorize_device(device_id: str) -> dict:
    """Autoriza un dispositivo pendiente."""
    _validate_device_id(device_id)
    url = f"{_BASE}/device/{device_id}/authorized"
    try:
        r = requests.post(url, headers=_headers(), json={"authorized": True}, timeout=30)
        r.raise_for_status()
        return {"authorized": True, "device_id": device_id}
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error(str(e)))


def delete_device(device_id: str) -> dict:
    """Elimina un dispositivo del tailnet."""
    _validate_device_id(device_id)
    url = f"{_BASE}/device/{device_id}"
    try:
        r = requests.delete(url, headers=_headers(), timeout=30)
        r.raise_for_status()
        return {"deleted": True, "device_id": device_id}
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error(str(e)))
