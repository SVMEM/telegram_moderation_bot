from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .text import compact_text, normalize_text

ACTION_SEVERITY = {
    "log_only": 0,
    "notify": 1,
    "delete": 2,
    "delete_warn": 3,
    "delete_restrict": 4,
    "delete_ban": 5,
}


@dataclass(frozen=True)
class Rule:
    id: int
    chat_id: int
    name: str
    rule_type: str
    pattern: str
    action: str = "delete"
    priority: int = 100
    status: str = "active"


@dataclass(frozen=True)
class WhitelistEntry:
    id: int
    chat_id: int
    entry_type: str
    value: str
    expires_at: str | None = None


@dataclass(frozen=True)
class RuleMatch:
    rule: Rule
    matched: str


@dataclass(frozen=True)
class Evaluation:
    allowed: bool
    whitelist: WhitelistEntry | None
    match: RuleMatch | None


def _not_expired(expires_at: str | None) -> bool:
    if not expires_at:
        return True
    try:
        until = datetime.fromisoformat(expires_at)
    except ValueError:
        return False
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until > datetime.now(timezone.utc)


def _whole_word(pattern: str, text: str) -> str | None:
    expression = re.compile(rf"(?<!\w){re.escape(pattern)}(?!\w)", re.UNICODE)
    found = expression.search(text)
    if found:
        return found.group(0)

    digits_suffix = re.compile(rf"(?<!\w){re.escape(pattern)}(?=\d+\b)", re.UNICODE)
    found = digits_suffix.search(text)
    if found:
        return found.group(0)

    if " " in pattern or len(pattern) < 2:
        return None

    separated = re.compile(
        r"(?<!\w)" + r"[\W_]*".join(re.escape(ch) for ch in pattern) + r"(?!\w)",
        re.UNICODE,
    )
    found = separated.search(text)
    return found.group(0) if found else None


def _mask_to_regex(pattern: str) -> re.Pattern[str]:
    normalized = normalize_text(pattern)
    expression = re.escape(normalized).replace(r"\*", r"\w*")
    return re.compile(rf"(?<!\w){expression}(?!\w)", re.UNICODE)


def match_rule(rule: Rule, text: str) -> str | None:
    normalized_text = normalize_text(text)
    compact = compact_text(text)
    normalized_pattern = normalize_text(rule.pattern)
    compact_pattern = compact_text(rule.pattern)

    if not normalized_pattern:
        return None

    if rule.rule_type == "exact":
        return _whole_word(normalized_pattern, normalized_text)

    if rule.rule_type == "contains":
        if normalized_pattern in normalized_text:
            return normalized_pattern
        return normalized_pattern if compact_pattern and compact_pattern in compact else None

    if rule.rule_type == "phrase":
        if normalized_pattern in normalized_text:
            return normalized_pattern
        return normalized_pattern if compact_pattern and compact_pattern in compact else None

    if rule.rule_type == "mask":
        found = _mask_to_regex(rule.pattern).search(normalized_text)
        return found.group(0) if found else None

    if rule.rule_type == "regex":
        try:
            found = re.search(normalized_pattern, normalized_text, re.IGNORECASE | re.UNICODE)
        except re.error:
            return None
        return found.group(0) if found else None

    return None


def evaluate_message(
    *,
    chat_id: int,
    text: str,
    author_id: int | None,
    sender_chat_id: int | None,
    rules: list[Rule],
    whitelist: list[WhitelistEntry],
) -> Evaluation:
    normalized_text = normalize_text(text)
    compact = compact_text(text)

    for entry in whitelist:
        if entry.chat_id != chat_id or not _not_expired(entry.expires_at):
            continue
        if entry.entry_type == "user":
            allowed_ids = {str(value) for value in (author_id, sender_chat_id) if value is not None}
            if entry.value in allowed_ids:
                return Evaluation(True, entry, None)
        if entry.entry_type == "expr":
            normalized_value = normalize_text(entry.value)
            compact_value = compact_text(entry.value)
            if normalized_value in normalized_text or (compact_value and compact_value in compact):
                return Evaluation(True, entry, None)

    active_rules = [rule for rule in rules if rule.chat_id == chat_id and rule.status == "active"]
    active_rules.sort(
        key=lambda rule: (
            -rule.priority,
            -ACTION_SEVERITY.get(rule.action, ACTION_SEVERITY["delete"]),
            rule.id,
        )
    )

    for rule in active_rules:
        matched = match_rule(rule, text)
        if matched:
            return Evaluation(False, None, RuleMatch(rule, matched))

    return Evaluation(False, None, None)
