"""Secret scrubber for a2a records."""

from __future__ import annotations

import re

PATTERNS = [
    ("api_key_sk", re.compile(r"\bsk-[a-zA-Z0-9_-]{20,}(?![a-zA-Z0-9_-])")),
    ("stripe_key", re.compile(r"\b(?:sk|rk)_(?:live|test)_[a-zA-Z0-9]{20,}\b")),
    ("npm_token", re.compile(r"\bnpm_[a-zA-Z0-9]{20,}\b")),
    ("huggingface_token", re.compile(r"\bhf_[a-zA-Z0-9]{20,}\b")),
    ("github_token", re.compile(r"\bgh[puors]_[a-zA-Z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{16,}(?![a-zA-Z0-9_])")),
    ("google_api_key", re.compile(r"\bAIza[a-zA-Z0-9_-]{20,}(?![a-zA-Z0-9_-])")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{24,}(?![A-Za-z0-9._~+/-])", re.IGNORECASE)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_-]{10,}){2}(?![A-Za-z0-9_-])")),
    ("messenger_bot_token", re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{30,45}(?![A-Za-z0-9_-])")),
    ("gocspx", re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{16,}(?![A-Za-z0-9_-])")),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,}(?![0-9a-zA-Z-])")),
    ("pem_private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")),
    ("pgp_private_block", re.compile(r"-----BEGIN PGP PRIVATE KEY BLOCK-----")),
    ("slack_webhook", re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+")),
    ("gitlab_pat", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("x_app_token", re.compile(r"\bxapp-[0-9A-Za-z-]{20,}(?![0-9A-Za-z-])")),
    ("discord_bot", re.compile(r"\b[MN][A-Za-z0-9_]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b")),
    ("gcp_oauth", re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])")),
    ("url_basic_auth", re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.I)),
    ("url_secret", re.compile(r"[?&#](?:access_token|refresh_token|id_token|client_secret|api_key|api-key|apikey|token|password|secret|session_token|jwt)=[^#&\s]+", re.IGNORECASE)),
    ("hermes_auth_path", re.compile(r"/home/[^/\s]+/\.hermes/auth\S*")),
    # v0.6.0 §1 (C-05): ключ=value / yaml формы — СВЕРХ существующих токено-
    # паттернов. Значение никогда не эхомится: redact заменяет пару на маркер.
    # (?!\[REDACTED) не даёт вырезать уже отредактированные legacy-маркеры.
    ("kv_secret", re.compile(
        r"(?i)\b(?:password|passwd|pwd|token|api_?key|api-key|apikey|secret|"
        r"access_key|secret_key|auth_token|credential)s?\s*=\s*(?!\[REDACTED)[^\s;,]+")),
    ("yaml_secret", re.compile(
        r"(?i)\b(?:password|passwd|pwd|token|api_?key|api-key|apikey|secret|"
        r"access_key|secret_key|auth_token|credential)s?\s*:\s*(?!\[REDACTED)[^\n]+")),
    ("hex_key_32", re.compile(
        r"(?i)\b(?:key|token|secret|hex_?key|api_?key)\s*[:=]\s*(?!\[REDACTED)[0-9a-f]{32,}\b")),
]

# v0.6.0 новые паттерны: на hard-reject каналах (mail/think/user) они
# РЕДАКТИРУЮТСЯ (доставка не ломается), legacy-токены там по-прежнему reject —
# контракт v0.4.10 (test_mail_still_hard_rejects) не меняется.
NEW_PATTERNS = frozenset({"kv_secret", "yaml_secret", "hex_key_32"})


def scan(text: str) -> list[str]:
    """Return secret pattern names only; never echo matched credential text."""
    hits = []
    for name, pat in PATTERNS:
        for _ in pat.finditer(text):
            hits.append(name)
    return hits


def check(text: str) -> tuple[bool, list[str]]:
    hits = scan(text)
    return bool(hits), hits


# Секреты, которые НЕВОЗМОЖно безопасно вырезать построчно (тело PEM-ключа /
# секрет в query-URL продолжается после совпадающего фрагмента) — только reject.
UNREDACTABLE = frozenset({"pem_private_key", "pgp_private_block", "url_secret"})


def scrub_echo(text: str, cap: int = 400) -> str:
    """Очистить вывод команды перед эхом/персистом: вырезать токены, урезать длину.

    Если после redact остаётся UNREDACTABLE-совпадение (PEM/PGP/url-secret) —
    заменяем ВСЮ строку на маркер (тело секрета не должен видеть ни stdout, ни файл).
    """
    if text is None:
        return ""
    out = str(text)
    if set(scan(out)) & UNREDACTABLE:
        return "[verify_output_suppressed]"
    out, _ = redact(out)
    return out[:cap]


def hard_channel_scrub(text: str) -> tuple[str, list[str], str]:
    """Политика hard-каналов (mail/think/user): legacy = reject, новые = redact.

    Возвращает (чистый_текст, hits, action), action ∈ clean|redacted|reject.
    UNREDACTABLE (PEM/PGP/url-secret) — всегда reject: безопасно не вырезать.
    """
    hits = scan(text)
    if not hits:
        return text, [], "clean"
    hs = set(hits)
    if hs & UNREDACTABLE or not hs <= NEW_PATTERNS:
        return text, hits, "reject"
    out, _ = redact(text)
    return out, hits, "redacted"


def scrub_value(value):
    """v0.6.0 §1: редактировать строки ВНУТРИ структуры (meta/списки) до
    сериализации. Пофилодно: redact по уже svdump'ленному yaml мог съесть
    закрывающую кавычку и сломать front-matter."""
    if isinstance(value, str):
        return redact(value)[0]
    if isinstance(value, list):
        return [scrub_value(v) for v in value]
    if isinstance(value, dict):
        return {k: scrub_value(v) for k, v in value.items()}
    return value


def redact(text: str) -> tuple[str, list[str]]:
    """Заменить известные форматы токенов на [REDACTED:<NAME>] (P0-2 redact-and-write).

    Возвращает (чистый_текст, список имён сработавших паттернов).
    Вызывать только если scan не вернул UNREDACTABLE-паттерны.
    """
    hits = scan(text)
    out = text
    for name, pat in PATTERNS:
        if name in hits:
            out = pat.sub(f"[REDACTED:{name.upper()}]", out)
    return out, hits
