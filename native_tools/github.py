"""GitHub tools — nativas en Python con PyGithub."""
import re
import warnings

try:
    from github import Github
except ImportError:
    Github = None  # type: ignore

from .secrets import load as _load_secret

# GitHub usernames/repos: debe empezar por alfanumérico + hyphens/underscores/dots
# Rechaza `.hidden`, `-start` y cadena vacía (inválidos en GitHub).
_GH_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
# PR/issue state
_GH_STATE_RE = re.compile(r"^(open|closed|all)$")


def _client():
    if Github is None:
        raise RuntimeError("PyGithub no está instalado. Ejecuta: uv add PyGithub")
    try:
        token = _load_secret("GITHUB_TOKEN")
    except RuntimeError:
        token = ""
    if token:
        return Github(token)
    warnings.warn(
        "GITHUB_TOKEN no configurado: usando cliente anonymous (rate limit 60 req/h). "
        "Configura GITHUB_TOKEN en env, secrets/*.md o .env para rate limit 5000 req/h.",
        UserWarning,
        stacklevel=2,
    )
    return Github()  # Anónimo, rate limit bajo


def _validate_name(name: str, field: str = "name") -> None:
    if not name or not _GH_NAME_RE.match(name):
        raise ValueError(f"{field} inválido")


def _validate_state(state: str) -> None:
    if not state or not _GH_STATE_RE.match(state):
        raise ValueError("state debe ser 'open', 'closed' o 'all'")


def list_repos(user: str) -> list[dict]:
    """Lista repositorios de un usuario u organización."""
    _validate_name(user, "user")
    g = _client()
    u = g.get_user(user)
    return [{"name": r.name, "url": r.html_url, "private": r.private} for r in u.get_repos()]


def get_repo_info(owner: str, repo: str) -> dict:
    """Información de un repositorio."""
    _validate_name(owner, "owner")
    _validate_name(repo, "repo")
    g = _client()
    r = g.get_repo(f"{owner}/{repo}")
    return {
        "name": r.name,
        "description": r.description,
        "stars": r.stargazers_count,
        "forks": r.forks_count,
        "open_issues": r.open_issues_count,
        "language": r.language,
        "url": r.html_url,
    }


def get_issue(owner: str, repo: str, issue_number: int) -> dict:
    """Detalles de una issue."""
    _validate_name(owner, "owner")
    _validate_name(repo, "repo")
    if not isinstance(issue_number, int) or issue_number < 1:
        raise ValueError("issue_number debe ser un entero positivo")
    g = _client()
    r = g.get_repo(f"{owner}/{repo}")
    i = r.get_issue(issue_number)
    return {
        "number": i.number,
        "title": i.title,
        "state": i.state,
        "body": i.body,
        "url": i.html_url,
    }


def create_issue(owner: str, repo: str, title: str, body: str = "") -> dict:
    """Crea una nueva issue."""
    _validate_name(owner, "owner")
    _validate_name(repo, "repo")
    if not title or not isinstance(title, str):
        raise ValueError("title es obligatorio y debe ser string")
    if not isinstance(body, str):
        raise ValueError("body debe ser string")
    g = _client()
    r = g.get_repo(f"{owner}/{repo}")
    i = r.create_issue(title=title, body=body)
    return {
        "number": i.number,
        "title": i.title,
        "url": i.html_url,
    }


def list_prs(owner: str, repo: str, state: str = "open") -> list[dict]:
    """Lista pull requests abiertos."""
    _validate_name(owner, "owner")
    _validate_name(repo, "repo")
    _validate_state(state)
    g = _client()
    r = g.get_repo(f"{owner}/{repo}")
    return [
        {
            "number": p.number,
            "title": p.title,
            "state": p.state,
            "url": p.html_url,
        }
        for p in r.get_pulls(state=state)
    ]
