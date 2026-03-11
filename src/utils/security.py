"""
src/utils/security.py
──────────────────────
Security utilities for safe repository ingestion.

Responsibilities
----------------
* Validate and canonicalise local paths (prevent path traversal).
* Validate GitHub URLs (allow-list format only).
* Enforce repo/file size limits before analysis.
* Sanitise strings destined for shell commands (no subprocess injection).
* Block dangerous file extensions from being read into memory.

Branch: feature/01-project-setup
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from src.utils.logging_config import get_logger

logger = get_logger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
# Extensions we will NEVER read into memory (binary, executables, etc.)
_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe", ".dll", ".so", ".dylib", ".bin", ".img", ".iso",
        ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
        ".mp3", ".mp4", ".avi", ".mov", ".mkv",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".ico",
        ".pdf", ".docx", ".xlsx", ".pptx",
        ".pyc", ".pyo", ".pyd",
        ".db", ".sqlite", ".sqlite3",
        ".key", ".pem", ".p12", ".pfx", ".crt", ".cer",  # crypto material
    }
)

# Strict allow-list for GitHub URLs
_GITHUB_URL_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?(?:/.*)?$"
)

# Default resource ceilings (overridable via env / settings)
DEFAULT_MAX_REPO_SIZE_MB: int = 500
DEFAULT_MAX_FILE_SIZE_KB: int = 500
DEFAULT_MAX_DEPTH: int = 20


# ── Public API ─────────────────────────────────────────────────────────────────

class SecurityError(Exception):
    """Raised when an input fails a security check."""


def validate_local_path(raw_path: str, base_dir: Path | None = None) -> Path:
    """
    Resolve *raw_path* to a canonical absolute path.

    Raises
    ------
    SecurityError
        If the resolved path escapes *base_dir* (path traversal),
        does not exist, or is not a directory.
    """
    try:
        resolved = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SecurityError(f"Cannot resolve path '{raw_path}': {exc}") from exc

    if base_dir is not None:
        try:
            resolved.relative_to(base_dir.resolve())
        except ValueError as exc:
            raise SecurityError(
                f"Path '{resolved}' escapes the allowed base directory '{base_dir}'."
            ) from exc

    if not resolved.is_dir():
        raise SecurityError(f"Path '{resolved}' is not a directory.")

    logger.debug("path_validated", resolved=str(resolved))
    return resolved


def validate_github_url(url: str) -> str:
    """
    Validate that *url* is a well-formed GitHub HTTPS URL.

    Returns the sanitised URL string.

    Raises
    ------
    SecurityError
        If the URL does not match the expected pattern.
    """
    url = url.strip()
    if not _GITHUB_URL_RE.match(url):
        raise SecurityError(
            f"Invalid GitHub URL '{url}'. "
            "Only https://github.com/<owner>/<repo> URLs are accepted."
        )
    logger.debug("github_url_validated", url=url)
    return url


def check_repo_size(repo_path: Path, max_mb: int = DEFAULT_MAX_REPO_SIZE_MB) -> None:
    """
    Walk *repo_path* and raise SecurityError if total size exceeds *max_mb* MB.
    Skips symlinks and blocked extensions to avoid inflating the estimate.
    """
    total_bytes = 0
    for root, _dirs, files in os.walk(repo_path):
        # Skip hidden directories (e.g. .git)
        _dirs[:] = [d for d in _dirs if not d.startswith(".")]
        for fname in files:
            fpath = Path(root) / fname
            if fpath.suffix.lower() in _BLOCKED_EXTENSIONS:
                continue
            if fpath.is_symlink():
                continue
            try:
                total_bytes += fpath.stat().st_size
            except OSError:
                continue

    total_mb = total_bytes / (1024 * 1024)
    if total_mb > max_mb:
        raise SecurityError(
            f"Repository size {total_mb:.1f} MB exceeds the maximum allowed "
            f"{max_mb} MB. Use a smaller target or raise MAX_REPO_SIZE_MB."
        )
    logger.info("repo_size_ok", size_mb=round(total_mb, 2))


def is_safe_file(path: Path, max_kb: int = DEFAULT_MAX_FILE_SIZE_KB) -> bool:
    """
    Return True if *path* is safe to read into memory:
    - Not a symlink (avoid symlink attacks).
    - Extension not in the blocked list.
    - File size does not exceed *max_kb* KB.
    """
    if path.is_symlink():
        logger.debug("skipping_symlink", path=str(path))
        return False

    if path.suffix.lower() in _BLOCKED_EXTENSIONS:
        logger.debug("skipping_blocked_extension", path=str(path))
        return False

    try:
        size_kb = path.stat().st_size / 1024
    except OSError:
        return False

    if size_kb > max_kb:
        logger.debug("skipping_oversized_file", path=str(path), size_kb=round(size_kb, 1))
        return False

    return True


def sanitise_for_shell(value: str) -> str:
    """
    Return a shell-safe version of *value* by stripping characters that
    could enable command injection.

    This is a defence-in-depth measure. All subprocess calls should use
    list-form arguments (never shell=True) — this function is a secondary guard.
    """
    # Allow alphanumerics, common path chars, and safe punctuation only
    return re.sub(r"[^A-Za-z0-9_./ -]", "", value)


def get_disk_free_mb(path: Path) -> float:
    """Return free disk space in MB at *path*."""
    usage = shutil.disk_usage(path)
    return usage.free / (1024 * 1024)
