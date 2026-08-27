"""A conservative, round-trip parser for the text-based OpenFAST formats.

OpenFAST module files do not share one formal grammar.  The common scalar form
is ``value  KeyName  - description``; tables and free-form sections are left
untouched.  This parser intentionally edits only recognized scalar records so
that opening and saving an input deck cannot silently destroy module data.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
import shlex
from typing import Any, Iterable


KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_()\[\]]*$")
REFERENCE_EXTENSIONS = {
    ".fst", ".dat", ".txt", ".inp", ".ipt", ".yaml", ".yml", ".dll", ".so", ".dylib"
}


@dataclass(frozen=True)
class Parameter:
    key: str
    value: Any
    raw_value: str
    description: str
    line: int
    kind: str
    reference: str | None = None

    def json(self) -> dict[str, Any]:
        return asdict(self)


def _tokens(text: str) -> list[str]:
    try:
        return shlex.split(text, comments=False, posix=True)
    except ValueError:
        return text.split()


def parse_value(raw: str) -> tuple[Any, str]:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true", "boolean"
    if lowered in {"default", "unused", "none"}:
        return raw, "keyword"
    unquoted = raw[1:-1] if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'" else raw
    try:
        if re.fullmatch(r"[+-]?\d+", raw):
            return int(raw), "integer"
        return float(raw.replace("D", "E").replace("d", "e")), "number"
    except ValueError:
        return unquoted, "string"


def parse_line(line: str, line_number: int) -> Parameter | None:
    stripped = line.strip()
    if not stripped or stripped[0] in "!#=-" or stripped.lower().startswith("end"):
        return None
    tokens = _tokens(stripped)
    if len(tokens) < 2 or not KEY_RE.fullmatch(tokens[1]):
        return None
    # Table headings usually contain several identifier-like columns.  Scalar
    # keys are followed by a dash/comment or end after the key.
    if len(tokens) > 2 and tokens[2] not in {"-", "!"} and KEY_RE.fullmatch(tokens[0]):
        return None
    raw_value, key = tokens[0], tokens[1]
    value, kind = parse_value(raw_value)
    dash = stripped.find("-", stripped.find(key) + len(key))
    bang = stripped.find("!", stripped.find(key) + len(key))
    markers = [index for index in (dash, bang) if index >= 0]
    description = stripped[min(markers) + 1 :].strip() if markers else ""
    reference = None
    if isinstance(value, str) and value and value.lower() not in {"unused", "none", "default"}:
        candidate = Path(value)
        if candidate.suffix.lower() in REFERENCE_EXTENSIONS:
            reference = value
    return Parameter(key, value, raw_value, description, line_number, kind, reference)


def parse_text(text: str) -> list[Parameter]:
    return [parameter for i, line in enumerate(text.splitlines(), 1) if (parameter := parse_line(line, i))]


def parameter_map(parameters: Iterable[Parameter]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for parameter in parameters:
        if parameter.key in result:
            current = result[parameter.key]
            result[parameter.key] = current + [parameter.value] if isinstance(current, list) else [current, parameter.value]
        else:
            result[parameter.key] = parameter.value
    return result


def parse_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace")
    parameters = parse_text(text)
    stat = path.stat()
    return {
        "path": str(path),
        "parameters": [parameter.json() for parameter in parameters],
        "data": parameter_map(parameters),
        "line_count": len(text.splitlines()),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _format_value(value: Any, original: str) -> str:
    if isinstance(value, bool):
        rendered = "True" if value else "False"
    elif isinstance(value, (int, float)):
        rendered = str(value)
    else:
        rendered = str(value)
        if any(char.isspace() for char in rendered) or (original.startswith(('"', "'"))):
            quote = original[0] if original.startswith(('"', "'")) else '"'
            rendered = f"{quote}{rendered}{quote}"
    return rendered


def update_file(path: Path, updates: dict[str, Any], expected_mtime_ns: int | None = None) -> dict[str, Any]:
    if expected_mtime_ns is not None and path.stat().st_mtime_ns != expected_mtime_ns:
        raise RuntimeError("File changed on disk; reload it before saving")
    original = path.read_text(encoding="utf-8", errors="replace")
    newline = "\r\n" if "\r\n" in original else "\n"
    trailing = original.endswith(("\n", "\r"))
    lines = original.splitlines()
    remaining = dict(updates)
    for index, line in enumerate(lines):
        parameter = parse_line(line, index + 1)
        if not parameter or parameter.key not in remaining:
            continue
        match = re.match(r"^(\s*)(?:\"[^\"]*\"|'[^']*'|\S+)(.*)$", line)
        if match:
            lines[index] = match.group(1) + _format_value(remaining.pop(parameter.key), parameter.raw_value) + match.group(2)
    if remaining:
        raise KeyError(f"Unknown parameter(s): {', '.join(sorted(remaining))}")
    path.write_text(newline.join(lines) + (newline if trailing else ""), encoding="utf-8", newline="")
    return parse_file(path)

