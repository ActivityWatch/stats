"""Conservative structural signatures for reports without a Play issue ID.

These are versioned heuristics, not authoritative crash clusters. Play's
reportText is human-readable and not machine-stable; unsupported lines are
dropped, so incomplete/unsymbolicated reports can share a fallback signature.
No exception messages, source paths, or unparsed text enter the signature.
"""
from __future__ import annotations

import hashlib
import json
import re


_IDENT = r"[A-Za-z_$][A-Za-z0-9_$]*"
_JAVA_METHOD = rf"(?:{_IDENT}\.)+(?:{_IDENT}|<init>|<clinit>)"
_CLASS = rf"(?:{_IDENT}\.)*(?:[A-Z_$][A-Za-z0-9_$]*)?(?:Exception|Error|Throwable)"
_CAUSE = re.compile(
    rf'^(?:(?:Caused by:\s*|Exception\s+|Exception in thread "[^"]*"\s+))?'
    rf"({_CLASS})(?::|$)"
)
_SIGNAL_NAME = r"SIG(?:ABRT|SEGV|BUS|ILL|FPE|TRAP|SYS|KILL|QUIT)"
_SIGNAL = re.compile(
    rf"^(?:(?:Fatal\s+)?signal\s+\d+\s+\(({_SIGNAL_NAME})\)|({_SIGNAL_NAME})(?::|$))"
)
_LOGCAT = re.compile(
    r"^(?:(?:\d{4}-)?\d{2}-\d{2}\s+)?\d{2}:\d{2}:\d{2}(?:\.\d+)?\s+"
    r"(?:(?:\d+\s+){2})?[VDIWEFAS]\s+[^:\s]+\s*:\s*(.*)$"
)
_JAVA_FRAME = re.compile(rf"^at\s+({_JAVA_METHOD})\s*\([^()]*\)$")
_NATIVE_FRAME = re.compile(
    r"^#\d+\s+(?:pc\s+[0-9a-fA-F]+\s+\S+|\.\.\.)\s+"
    r"(?:\(offset 0x[0-9a-fA-F]+\)\s+)?\(([^()]*)\)"
    r"(?:\s+\(BuildId:\s*[0-9a-fA-F]+\))?$"
)
_ABBREVIATED_JAVA = re.compile(rf"^#\d+\s+\.\.\.\s+({_JAVA_METHOD})$")
_RUST_FRAME = re.compile(r"^\d+:\s+(?:0x[0-9a-fA-F]+\s+-\s+)?(\S+::\S+)$")
_NATIVE_SYMBOL = re.compile(rf"{_IDENT}(?:::{_IDENT})*")
_RUST_HASH = re.compile(r"::h[0-9a-f]{16}$")
_OFFSET = re.compile(r"\+(?:0x[0-9a-fA-F]+|\d+)$")


def _native_symbol(value: str) -> str | None:
    symbol = _RUST_HASH.sub("", _OFFSET.sub("", value.strip()))
    return symbol if _NATIVE_SYMBOL.fullmatch(symbol) else None


def normalize_trace(text: str) -> dict[str, list[str]]:
    """Extract ordered cause classes/signals and stack symbols, dropping prose.

    Supports Java/Kotlin frames, Android native frames, and simple symbolicated
    Rust backtraces. Complex C++/Rust demangled symbols are deliberately omitted.
    This reduces incidental data exposure; it is not a general PII redactor.
    """
    causes: list[str] = []
    frames: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        logcat = _LOGCAT.fullmatch(line)
        if logcat:
            line = logcat.group(1).strip()
        cause = _CAUSE.match(line)
        if cause:
            causes.append(cause.group(1))
            continue
        signal = _SIGNAL.match(line)
        if signal:
            causes.append(signal.group(1) or signal.group(2))
            continue
        java = _JAVA_FRAME.fullmatch(line) or _ABBREVIATED_JAVA.fullmatch(line)
        if java:
            frames.append(java.group(1))
            continue
        native = _NATIVE_FRAME.fullmatch(line) or _RUST_FRAME.fullmatch(line)
        if native:
            symbol = _native_symbol(native.group(1))
            if symbol:
                frames.append(symbol)
    return {"causes": causes, "frames": frames}


def _normalize_location(location: str) -> str:
    """Keep only a method, qualified native symbol, or shared-library basename."""
    location = location.strip()
    java = re.fullmatch(rf"({_JAVA_METHOD})(?:\s*\([^()]*\))?", location)
    if java:
        return java.group(1)
    if "::" in location or location.startswith("Java_"):
        return _native_symbol(location) or ""
    library = location.rsplit("/", 1)[-1]
    if re.fullmatch(r"lib[A-Za-z0-9_-]+\.so", library):
        return library
    return ""


def fallback_fingerprint(
    issue_type: str, cause: str, location: str, trace: str = ""
) -> str:
    """Hash structural evidence when Play did not provide an issue ID.

    Changes to this normalization contract require a new version prefix. Missing
    structure can collide; report consumers must label this key as heuristic.
    """
    known_types = {"CRASH", "ANR", "NON_FATAL"}
    issue_type = issue_type.strip().upper()
    if issue_type == "APPLICATION_NOT_RESPONDING":
        issue_type = "ANR"
    payload = {
        "type": issue_type if issue_type in known_types else "UNKNOWN",
        "cause": normalize_trace(cause)["causes"],
        "location": _normalize_location(location),
        "trace": normalize_trace(trace),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return "fallback:v1:" + hashlib.sha256(encoded).hexdigest()
