from __future__ import annotations

from pathlib import Path
from typing import Any

from .parser import parse_file


def safe_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Path escapes the configured model directory")
    if not candidate.is_file():
        raise FileNotFoundError(relative)
    return candidate


def discover_models(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    return [
        {"name": path.stem, "path": path.relative_to(root).as_posix(), "size": path.stat().st_size}
        for path in sorted(root.rglob("*.fst"))
    ]


def referenced_files(root: Path, entry_relative: str, limit: int = 250) -> dict[str, Any]:
    entry = safe_path(root, entry_relative)
    root = root.resolve()
    queue = [entry]
    visited: set[Path] = set()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    while queue and len(visited) < limit:
        path = queue.pop(0).resolve()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        parsed = parse_file(path)
        rel = path.relative_to(root).as_posix()
        nodes.append({
            "path": rel,
            "name": path.name,
            "parameter_count": len(parsed["parameters"]),
            "size": parsed["size"],
        })
        for parameter in parsed["parameters"]:
            reference = parameter.get("reference")
            if not reference:
                continue
            child = (path.parent / reference).resolve()
            if root not in child.parents and child != root:
                continue
            if child.is_file():
                child_rel = child.relative_to(root).as_posix()
                edges.append({"from": rel, "to": child_rel, "key": parameter["key"]})
                if child not in visited:
                    queue.append(child)
    return {"entry": entry_relative, "files": nodes, "references": edges, "truncated": bool(queue)}


def model_geometry(root: Path, entry_relative: str) -> dict[str, float | str]:
    graph = referenced_files(root, entry_relative)
    merged: dict[str, Any] = {}
    for node in graph["files"]:
        parsed = parse_file(safe_path(root, node["path"]))
        merged.update(parsed["data"])

    def number(keys: tuple[str, ...], fallback: float) -> float:
        for key in keys:
            value = merged.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return fallback

    hub_height = number(("TowerHt", "HubHt"), 150.0)
    blade_length = number(("TipRad", "BladeLength"), 120.0) - number(("HubRad",), 3.0)
    return {
        "hubHeight": max(20.0, hub_height),
        "bladeLength": max(5.0, blade_length),
        "overhang": number(("OverHang", "Overhang"), 10.0),
        "platformDraft": abs(number(("PtfmCMzt", "PtfmRefzt"), 20.0)),
        "source": "OpenFAST parameters",
    }

