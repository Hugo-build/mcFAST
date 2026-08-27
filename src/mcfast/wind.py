"""Workspace-local InflowWind and TurbSim configuration helpers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .models import referenced_files, safe_path
from .parser import parse_file


def _within(root: Path, candidate: Path) -> bool:
    root = root.resolve()
    candidate = candidate.resolve()
    return candidate == root or root in candidate.parents


def project_path(root: Path, value: str, *, base: Path | None = None) -> Path:
    """Resolve an existing or prospective path while enforcing workspace isolation."""
    root = root.resolve()
    raw = Path(value).expanduser()
    candidate = (raw if raw.is_absolute() else (base or root) / raw).resolve()
    if not _within(root, candidate):
        raise ValueError("Wind path escapes the workspace project")
    return candidate


def find_inflow_file(root: Path, entry_relative: str) -> tuple[str, dict[str, Any]] | None:
    """Return the linked scalar file that owns WindType and FileName_BTS."""
    graph = referenced_files(root, entry_relative)
    for node in graph["files"]:
        parsed = parse_file(safe_path(root, node["path"]))
        if "WindType" in parsed["data"] and "FileName_BTS" in parsed["data"]:
            return node["path"], parsed
    return None


def discover_turbsim_inputs(root: Path) -> list[dict[str, Any]]:
    root = root.resolve()
    candidates: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.in")):
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        output = path.with_suffix(".bts")
        candidates.append({
            "path": relative,
            "name": path.name,
            "size": path.stat().st_size,
            "output_path": output.relative_to(root).as_posix(),
        })
    return candidates


def wind_status(
    root: Path,
    entry_relative: str,
    manifest: dict[str, Any],
    *,
    turbsim_executable: str | None,
) -> dict[str, Any]:
    root = root.resolve()
    candidates = discover_turbsim_inputs(root)
    found = find_inflow_file(root, entry_relative)
    base: dict[str, Any] = {
        "mode": "inactive",
        "active": False,
        "valid": True,
        "message": "The selected deck does not expose a linked InflowWind mode-3 input.",
        "inflow_file": None,
        "inflow_mtime_ns": None,
        "wind_type": None,
        "file_name_bts": None,
        "resolved_bts": None,
        "bts_exists": False,
        "bts_size": 0,
        "bts_stale": False,
        "turbsim_inputs": candidates,
        "selected_turbsim_input": manifest.get("wind", {}).get("turbsim_input"),
        "managed_bts": None,
        "turbsim_available": bool(turbsim_executable),
        "needs_generation": False,
    }
    if not found:
        return base

    inflow_relative, parsed = found
    inflow_path = safe_path(root, inflow_relative)
    wind_type = parsed["data"].get("WindType")
    raw_bts = parsed["data"].get("FileName_BTS")
    base.update({
        "inflow_file": inflow_relative,
        "inflow_mtime_ns": str(parsed["mtime_ns"]),
        "wind_type": wind_type,
        "file_name_bts": raw_bts,
    })
    if wind_type != 3:
        base["message"] = "TurbSim is inactive because WindType is not 3."
        return base

    base["active"] = True
    selected_relative = base["selected_turbsim_input"]
    selected_path: Path | None = None
    managed_bts: Path | None = None
    selected_error: str | None = None
    if selected_relative:
        try:
            selected_path = project_path(root, selected_relative)
            if selected_path.suffix.lower() != ".in" or not selected_path.is_file():
                selected_error = "The selected TurbSim input no longer exists."
                selected_path = None
            else:
                managed_bts = selected_path.with_suffix(".bts")
                base["managed_bts"] = managed_bts.relative_to(root).as_posix()
        except ValueError as exc:
            selected_error = str(exc)

    resolved_bts: Path | None = None
    if isinstance(raw_bts, str) and raw_bts.strip() and raw_bts.lower() not in {"none", "unused", "default"}:
        try:
            resolved_bts = project_path(root, raw_bts, base=inflow_path.parent)
            base["resolved_bts"] = resolved_bts.relative_to(root).as_posix()
        except ValueError as exc:
            base.update(mode="external", valid=False, message=str(exc))
            return base

    if resolved_bts is None:
        base.update(
            mode="unconfigured",
            valid=False,
            message=selected_error or "Select a TurbSim input to configure the mode-3 wind field.",
        )
        return base

    exists = resolved_bts.is_file()
    size = resolved_bts.stat().st_size if exists else 0
    base.update(bts_exists=exists, bts_size=size)

    if selected_path is not None and managed_bts is not None and resolved_bts == managed_bts:
        stale = not exists or resolved_bts.stat().st_mtime_ns < selected_path.stat().st_mtime_ns
        input_data = parse_file(selected_path)["data"]
        base.update(
            mode="managed",
            bts_stale=stale,
            needs_generation=stale,
        )
        if input_data.get("WrADFF") is not True:
            base.update(valid=False, message="The selected TurbSim input must set WrADFF = True.")
        elif stale and not turbsim_executable:
            base.update(valid=False, message="TurbSim is required because the managed .bts is missing or stale.")
        elif stale:
            base["message"] = "The managed wind field will be generated before OpenFAST."
        else:
            base["message"] = "The managed wind field is current and will be reused."
        return base

    if resolved_bts.suffix.lower() != ".bts":
        base.update(mode="external", valid=False, message="FileName_BTS must reference a .bts file.")
    elif not exists or size <= 0:
        base.update(mode="external", valid=False, message="The external .bts file does not exist or is empty.")
    else:
        base.update(mode="external", message="Using an existing external .bts file; TurbSim generation is disabled.")
    return base


def managed_bts_reference(root: Path, inflow_relative: str, turbsim_relative: str) -> tuple[Path, str]:
    input_path = project_path(root, turbsim_relative)
    if input_path.suffix.lower() != ".in" or not input_path.is_file():
        raise ValueError("Select an existing workspace TurbSim .in file")
    inflow_path = safe_path(root, inflow_relative)
    output_path = input_path.with_suffix(".bts")
    reference = Path(os.path.relpath(output_path, inflow_path.parent)).as_posix()
    return input_path, reference
