"""
prompt_loader.py — Central loader for externalized prompt files.

Loads prompts from the prompts/ directory with:
 - Version metadata validation
 - SHA-256 checksum tracking (warns on unexpected changes)
 - In-memory caching with mtime-based invalidation
"""
import os
import re
import json
import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# Cache: {filename: (mtime, content)}
_cache: dict[str, tuple[float, Any]] = {}

# Stored checksums from previous load (populated at first load per file)
_known_checksums: dict[str, str] = {}


# ---------------------------------------------------------------------------
# INTERNAL HELPERS
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _prompt_path(filename: str) -> str:
    return os.path.join(_PROMPT_DIR, filename)


def _is_stale(filename: str) -> bool:
    """Return True if the file has been modified since last cache."""
    path = _prompt_path(filename)
    if filename not in _cache:
        return True
    cached_mtime, _ = _cache[filename]
    try:
        return os.path.getmtime(path) != cached_mtime
    except OSError:
        return True


def _read_raw(filename: str) -> str:
    path = _prompt_path(filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_yaml_frontmatter(text: str) -> str:
    """Remove YAML frontmatter delimited by --- lines."""
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            return text[end + 3:].lstrip("\n")
    return text


def _check_and_warn(filename: str, content: str) -> None:
    """Warn if file content changed since last known checksum."""
    checksum = _sha256(content)
    if filename in _known_checksums:
        if _known_checksums[filename] != checksum:
            logger.warning(
                "⚠️ PROMPT CHANGED: %s (checksum mismatch). "
                "Old: %s… → New: %s…. Verify this was intentional.",
                filename,
                _known_checksums[filename][:12],
                checksum[:12],
            )
    _known_checksums[filename] = checksum


# ---------------------------------------------------------------------------
# PUBLIC API
# ---------------------------------------------------------------------------

def get_prompt(name: str) -> str:
    """
    Load a markdown prompt file (e.g. "empathetic_system").
    Returns the prompt text with YAML frontmatter stripped.
    """
    filename = f"{name}.md"
    if not _is_stale(filename) and filename in _cache:
        return _cache[filename][1]

    raw = _read_raw(filename)
    content = _strip_yaml_frontmatter(raw)
    _check_and_warn(filename, content)

    path = _prompt_path(filename)
    _cache[filename] = (os.path.getmtime(path), content)
    logger.info("📄 Loaded prompt: %s (v%s)", filename, _get_version(raw))
    return content


def get_seeds() -> list[dict]:
    """Load seed conversation exchanges."""
    filename = "seed_conversation.json"
    if not _is_stale(filename) and filename in _cache:
        return _cache[filename][1]

    raw = _read_raw(filename)
    data = json.loads(raw)
    exchanges = data.get("exchanges", [])
    _check_and_warn(filename, raw)

    path = _prompt_path(filename)
    _cache[filename] = (os.path.getmtime(path), exchanges)
    logger.info("📄 Loaded seeds: %d exchanges", len(exchanges))
    return exchanges


def get_grammar_fixes() -> dict:
    """
    Load grammar fix rules. Returns dict with keys:
      - hinglish_fixes: list of [pattern, replacement]
      - masculine_verb_fixes: list of [pattern, replacement]
      - masculine_verb_fixes_lambda: list of {pattern, find, replace} for lambda-based fixes
    """
    filename = "grammar_fixes.json"
    if not _is_stale(filename) and filename in _cache:
        return _cache[filename][1]

    raw = _read_raw(filename)
    data = json.loads(raw)
    _check_and_warn(filename, raw)

    # Strip metadata key
    result = {k: v for k, v in data.items() if k != "_meta"}

    path = _prompt_path(filename)
    _cache[filename] = (os.path.getmtime(path), result)
    logger.info("📄 Loaded grammar fixes: %d hinglish, %d masculine",
                len(result.get("hinglish_fixes", [])),
                len(result.get("masculine_verb_fixes", [])))
    return result


def _get_version(raw_text: str) -> str:
    """Extract version from YAML frontmatter if present."""
    match = re.search(r'version:\s*(\d+)', raw_text)
    return match.group(1) if match else "?"


def list_prompts() -> list[str]:
    """List all available prompt files."""
    try:
        return [f for f in os.listdir(_PROMPT_DIR)
                if not f.startswith("_") and not f.startswith(".")]
    except OSError:
        return []
