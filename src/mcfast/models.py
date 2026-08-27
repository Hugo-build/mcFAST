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


def _first_order_wamit_directory(hydro_file: Path) -> Path | None:
    hydro_data = next(
        (
            child for child in hydro_file.parent.iterdir()
            if child.is_dir() and child.name.casefold() == "hydrodata"
        ),
        None,
    )
    if hydro_data is None:
        return None
    for child in sorted(hydro_data.iterdir()):
        normalized = "".join(character for character in child.name.casefold() if character.isalnum())
        if child.is_dir() and "wamit" in normalized and (
            "1storder" in normalized or "firstorder" in normalized
        ):
            return child
    return None


def _find_floater_gdf(root: Path, graph: dict[str, Any]) -> Path | None:
    hydro_paths = [
        edge["to"] for edge in graph["references"]
        if edge["key"].casefold() == "hydrofile"
    ]
    for relative in hydro_paths:
        hydro_file = safe_path(root, relative)
        wamit_directory = _first_order_wamit_directory(hydro_file)
        if wamit_directory is None:
            continue
        candidates = sorted(wamit_directory.glob("*.[gG][dD][fF]"))
        if not candidates:
            continue
        pot_file = parse_file(hydro_file)["data"].get("PotFile")
        if isinstance(pot_file, str):
            wanted = Path(pot_file).name.casefold()
            exact = next((path for path in candidates if path.stem.casefold() == wanted), None)
            if exact is not None:
                return exact
        if len(candidates) == 1:
            return candidates[0]
    return None


def _parse_low_order_gdf(root: Path, path: Path) -> dict[str, Any] | None:
    """Convert a low-order WAMIT panel file into a compact triangle mesh."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) < 4:
        return None
    try:
        length_scale = float(lines[1].split()[0].replace("D", "E").replace("d", "e"))
        symmetry = [int(value) for value in lines[2].split()[:2]]
        panel_header = lines[3].split()
        panel_count = int(panel_header[0])
        # IGDEF=1 describes higher-order patches rather than four-corner panels.
        if len(panel_header) > 1:
            try:
                if int(panel_header[1]) != 0:
                    return None
            except ValueError:
                pass
    except (ValueError, IndexError):
        return None

    coordinate_lines = [line for line in lines[4:] if line.strip()]
    if len(coordinate_lines) < panel_count * 4:
        return None
    panels: list[list[tuple[float, float, float]]] = []
    try:
        for index in range(panel_count):
            panel = []
            for line in coordinate_lines[index * 4:index * 4 + 4]:
                values = [float(value.replace("D", "E").replace("d", "e")) for value in line.split()[:3]]
                panel.append(tuple(value * length_scale for value in values))
            panels.append(panel)
    except (ValueError, IndexError):
        return None

    reflections = [(1, 1)]
    if symmetry[0]:
        reflections += [(-1, 1)]
    if symmetry[1]:
        reflections += [(x_sign, -1) for x_sign, _ in list(reflections)]

    vertices: list[list[float]] = []
    indices: list[int] = []
    vertex_indices: dict[tuple[float, float, float], int] = {}
    emitted_panels: set[tuple[tuple[float, float, float], ...]] = set()

    def vertex_index(point: tuple[float, float, float]) -> int:
        # GDF uses z-up; the Three.js scene uses y-up.
        scene_point = (point[0], point[2], point[1])
        key = tuple(round(value, 7) for value in scene_point)
        if key not in vertex_indices:
            vertex_indices[key] = len(vertices)
            vertices.append(list(scene_point))
        return vertex_indices[key]

    for x_sign, y_sign in reflections:
        # Swapping GDF's y/z axes reverses handedness; an odd reflection
        # reverses it again.
        reverse_winding = x_sign * y_sign > 0
        for panel in panels:
            transformed = [(x * x_sign, y * y_sign, z) for x, y, z in panel]
            signature = tuple(sorted(tuple(round(value, 7) for value in point) for point in transformed))
            if signature in emitted_panels:
                continue
            emitted_panels.add(signature)
            corners: list[int] = []
            for point in transformed:
                index = vertex_index(point)
                if not corners or corners[-1] != index:
                    corners.append(index)
            if len(corners) > 2 and corners[0] == corners[-1]:
                corners.pop()
            if reverse_winding:
                corners.reverse()
            for index in range(1, len(corners) - 1):
                indices.extend((corners[0], corners[index], corners[index + 1]))

    if not indices:
        return None
    return {
        "source": path.relative_to(root.resolve()).as_posix(),
        "format": "WAMIT low-order GDF",
        "panelCount": len(emitted_panels),
        "vertices": vertices,
        "indices": indices,
    }


def model_geometry(root: Path, entry_relative: str) -> dict[str, Any]:
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
    gdf_path = _find_floater_gdf(root, graph)
    floater = _parse_low_order_gdf(root, gdf_path) if gdf_path is not None else None
    return {
        "hubHeight": max(20.0, hub_height),
        "bladeLength": max(5.0, blade_length),
        "overhang": number(("OverHang", "Overhang"), 10.0),
        "floater": floater,
        "source": "OpenFAST parameters",
    }
