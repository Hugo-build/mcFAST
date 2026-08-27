from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import random
import re
import shutil
import threading
from typing import Any
import uuid

from fastapi import FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .models import discover_models, model_geometry, referenced_files, safe_path
from .parser import parse_file, update_file
from .runner import find_openfast, find_turbsim, run_openfast, run_turbsim, turbsim_version
from .wind import find_inflow_file, managed_bts_reference, project_path, wind_status


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = Path(os.environ.get("MCFAST_MODEL_DIR", PROJECT_ROOT / "models")).resolve()
WORKSPACE_ROOT = Path(os.environ.get("MCFAST_WORKSPACE_DIR", PROJECT_ROOT / "workspaces")).resolve()
NATIVE_LIBRARY_SUFFIXES = {".dll", ".dylib", ".so"}
WORKSPACE_ID_RE = re.compile(r"[a-z0-9_-]+")
RUN_ID_RE = re.compile(r"[A-Za-z0-9_-]+")

app = FastAPI(title="mcFAST API", version="0.2.0")


class UpdateRequest(BaseModel):
    updates: dict[str, Any]
    expected_mtime_ns: int | None = None


class WorkspaceImportRequest(BaseModel):
    name: str
    source_path: str


class WindConfigurationRequest(BaseModel):
    turbsim_input: str
    expected_inflow_mtime_ns: int | None = None


class WorkspaceVariable(BaseModel):
    name: str
    file: str
    key: str
    minimum: float | None = None
    maximum: float | None = None


class SamplingRequest(BaseModel):
    method: str
    count: int | None = None
    seed: int | None = None
    csv_text: str | None = None


class StudyRequest(BaseModel):
    name: str
    variables: list[WorkspaceVariable]
    sampling: SamplingRequest


RUNS: dict[tuple[str, str], dict[str, Any]] = {}
RUNS_LOCK = threading.Lock()
WORKSPACE_RUN_LOCKS: dict[str, threading.Lock] = {}
WORKSPACE_RUN_LOCKS_LOCK = threading.Lock()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-_").lower()
    return slug[:48] or fallback


def _json_write(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _workspace_dir(workspace_id: str, require: bool = True) -> Path:
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise HTTPException(404, "Workspace not found")
    target = (WORKSPACE_ROOT / workspace_id).resolve()
    root = WORKSPACE_ROOT.resolve()
    if target != root and root not in target.parents:
        raise HTTPException(404, "Workspace not found")
    if require and not (target / "workspace.json").is_file():
        raise HTTPException(404, "Workspace not found")
    return target


def _workspace_manifest(workspace_id: str) -> dict[str, Any]:
    directory = _workspace_dir(workspace_id)
    try:
        manifest = json.loads((directory / "workspace.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(500, f"Workspace manifest is invalid: {workspace_id}") from exc
    if manifest.get("workspace_id") != workspace_id:
        raise HTTPException(500, f"Workspace manifest ID does not match: {workspace_id}")
    return manifest


def _project_root(workspace_id: str) -> Path:
    return _workspace_dir(workspace_id) / "project"


def _workspace_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_id": manifest["workspace_id"],
        "name": manifest["name"],
        "entry": manifest["entry"],
        "source_path": manifest["source_path"],
        "source_fingerprint": manifest["source_fingerprint"],
        "created_at": manifest["created_at"],
        "updated_at": manifest["updated_at"],
        "external_dependencies": manifest.get("external_dependencies", []),
    }


def _list_workspace_manifests() -> list[dict[str, Any]]:
    if not WORKSPACE_ROOT.is_dir():
        return []
    manifests: list[dict[str, Any]] = []
    for path in WORKSPACE_ROOT.iterdir():
        manifest_path = path / "workspace.json"
        if not path.is_dir() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("workspace_id") == path.name:
                manifests.append(manifest)
        except (OSError, json.JSONDecodeError):
            continue
    return sorted(manifests, key=lambda item: item.get("created_at", ""))


def _walk_source_references(entry: Path, limit: int = 500) -> tuple[list[Path], list[dict[str, Any]]]:
    """Walk scalar OpenFAST references without granting write access to the source."""
    queue = [entry.resolve()]
    visited: set[Path] = set()
    text_files: list[Path] = []
    references: list[dict[str, Any]] = []
    while queue and len(visited) < limit:
        path = queue.pop(0).resolve()
        if path in visited or not path.is_file():
            continue
        visited.add(path)
        if path.suffix.lower() in NATIVE_LIBRARY_SUFFIXES:
            continue
        text_files.append(path)
        parsed = parse_file(path)
        for parameter in parsed["parameters"]:
            reference = parameter.get("reference")
            if not reference:
                continue
            raw = Path(reference).expanduser()
            target = (raw if raw.is_absolute() else path.parent / raw).resolve()
            if not target.is_file():
                continue
            record = {
                "source_file": path,
                "key": parameter["key"],
                "raw": reference,
                "target": target,
                "native": target.suffix.lower() in NATIVE_LIBRARY_SUFFIXES,
            }
            references.append(record)
            if not record["native"] and target not in visited:
                queue.append(target)
    if queue:
        raise HTTPException(400, f"Source reference graph exceeds {limit} linked files")
    return text_files, references


def _source_root(entry: Path, files: list[Path]) -> Path:
    common = Path(os.path.commonpath([str(path) for path in files])).resolve()
    if common.is_file() or common == entry:
        common = common.parent
    forbidden = {Path(common.anchor).resolve(), Path.home().resolve(), WORKSPACE_ROOT.resolve()}
    if common in forbidden:
        raise HTTPException(400, "The inferred project root is too broad to copy safely")
    if WORKSPACE_ROOT.resolve() == entry or WORKSPACE_ROOT.resolve() in entry.parents:
        raise HTTPException(400, "A workspace cannot be imported as a source")
    return common


def _validate_copy_tree(source_root: Path) -> tuple[int, int]:
    file_count = 0
    byte_count = 0
    for path in source_root.rglob("*"):
        if path.is_symlink():
            raise HTTPException(400, f"Project source contains an unsupported symlink: {path}")
        if path.is_file():
            file_count += 1
            byte_count += path.stat().st_size
    return file_count, byte_count


def _import_workspace(body: WorkspaceImportRequest) -> dict[str, Any]:
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Enter a workspace name")
    if len(name) > 80:
        raise HTTPException(400, "Workspace name must be 80 characters or fewer")
    if any(item.get("name", "").casefold() == name.casefold() for item in _list_workspace_manifests()):
        raise HTTPException(409, f"A workspace named '{name}' already exists")

    source = Path(body.source_path).expanduser()
    if not source.is_absolute():
        source = MODEL_ROOT / source
    if source.is_symlink():
        raise HTTPException(400, "The source .fst may not be a symlink")
    source = source.resolve()
    if not source.is_file():
        raise HTTPException(404, f"Source .fst does not exist: {source}")
    if source.suffix.lower() != ".fst":
        raise HTTPException(400, "Workspace source must be an OpenFAST .fst deck")
    text_files, references = _walk_source_references(source)
    source_root = _source_root(source, text_files)
    file_count, byte_count = _validate_copy_tree(source_root)
    timestamp = _utc_now()
    workspace_id = f"{_slug(name, 'openfast-project')}-{timestamp.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    final_dir = _workspace_dir(workspace_id, require=False)
    staging_dir = WORKSPACE_ROOT / f".{workspace_id}.tmp-{uuid.uuid4().hex[:6]}"
    copied_root = staging_dir / "project" / source_root.name
    final_copied_root = final_dir / "project" / source_root.name
    entry_relative = (Path(source_root.name) / source.relative_to(source_root)).as_posix()

    external_dependencies: list[dict[str, str]] = []
    rewrites: dict[Path, dict[str, str]] = {}
    try:
        shutil.copytree(source_root, copied_root)
        for reference in references:
            target: Path = reference["target"]
            source_file: Path = reference["source_file"]
            copied_source = copied_root / source_file.relative_to(source_root)
            replacement: str | None = None
            if reference["native"] and source_root not in target.parents:
                replacement = str(target)
                external_dependencies.append({
                    "type": "native-library",
                    "path": str(target),
                    "referenced_by": (Path(source_root.name) / source_file.relative_to(source_root)).as_posix(),
                    "key": reference["key"],
                })
            elif Path(reference["raw"]).is_absolute() and (target == source_root or source_root in target.parents):
                replacement = str(final_copied_root / target.relative_to(source_root))
            if replacement is not None:
                rewrites.setdefault(copied_source, {})[reference["key"]] = replacement
        for copied_source, updates in rewrites.items():
            update_file(copied_source, updates)

        manifest = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "name": name,
            "created_at": timestamp.isoformat(),
            "updated_at": timestamp.isoformat(),
            "source_path": str(source),
            "source_root": str(source_root),
            "source_fingerprint": f"sha256:{_hash_file(source)}",
            "entry": entry_relative,
            "project_root": f"project/{source_root.name}",
            "file_count": file_count,
            "source_bytes": byte_count,
            "external_dependencies": external_dependencies,
        }
        _json_write(staging_dir / "workspace.json", manifest)
        (staging_dir / "studies").mkdir()
        (staging_dir / "results").mkdir()
        os.replace(staging_dir, final_dir)
    except HTTPException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise HTTPException(500, f"Workspace import failed: {exc}") from exc
    return _workspace_summary(manifest)


def _ensure_example_workspace() -> None:
    if _list_workspace_manifests() or not MODEL_ROOT.is_dir():
        return
    candidates = sorted(MODEL_ROOT.rglob("*UMaineSemi*.fst"))
    if not candidates:
        return
    _import_workspace(WorkspaceImportRequest(name="IEA 15 MW UMaineSemi", source_path=str(candidates[0])))


def _coerce_sample(value: str, kind: str, column: str, row: int) -> Any:
    try:
        if kind == "boolean":
            lowered = value.strip().lower()
            if lowered not in {"true", "false", "1", "0"}:
                raise ValueError
            return lowered in {"true", "1"}
        if kind == "integer":
            number = float(value)
            if not number.is_integer():
                raise ValueError
            return int(number)
        if kind == "number":
            return float(value)
        return value
    except ValueError as exc:
        raise HTTPException(400, f"Invalid value for '{column}' in CSV row {row}") from exc


def _make_samples(variables: list[dict[str, Any]], sampling: SamplingRequest) -> list[dict[str, Any]]:
    if sampling.method == "csv":
        if not sampling.csv_text or not sampling.csv_text.strip():
            raise HTTPException(400, "Choose a non-empty CSV file")
        if len(sampling.csv_text.encode("utf-8")) > 10 * 1024 * 1024:
            raise HTTPException(400, "CSV must be smaller than 10 MB")
        reader = csv.DictReader(StringIO(sampling.csv_text.lstrip("\ufeff")))
        headings = [heading.strip() for heading in (reader.fieldnames or []) if heading]
        required = [variable["name"] for variable in variables]
        missing = [name for name in required if name not in headings]
        if missing:
            raise HTTPException(400, f"CSV is missing column(s): {', '.join(missing)}")
        rows = []
        for index, raw in enumerate(reader, 2):
            raw = {(key or "").strip(): value for key, value in raw.items()}
            if not any((value or "").strip() for value in raw.values()):
                continue
            rows.append({
                variable["name"]: _coerce_sample(
                    raw.get(variable["name"], "") or "", variable["kind"], variable["name"], index
                )
                for variable in variables
            })
        if not rows:
            raise HTTPException(400, "CSV contains no sample rows")
        return rows

    if sampling.method not in {"uniform", "random"}:
        raise HTTPException(400, "Sampling method must be uniform, random, or csv")
    count = sampling.count or 0
    if count < 1 or count > 100000:
        raise HTTPException(400, "Sample count must be between 1 and 100,000")
    if count * len(variables) > 1_000_000:
        raise HTTPException(400, "The sample set may contain at most 1,000,000 values")
    for variable in variables:
        if variable["kind"] not in {"number", "integer"}:
            raise HTTPException(400, f"Use CSV sampling for non-numeric variable '{variable['name']}'")
        if variable["minimum"] is None or variable["maximum"] is None:
            raise HTTPException(400, f"Set a range for '{variable['name']}'")
        if variable["minimum"] > variable["maximum"]:
            raise HTTPException(400, f"Minimum exceeds maximum for '{variable['name']}'")

    generator = random.Random(sampling.seed)
    rows: list[dict[str, Any]] = []
    for index in range(count):
        row: dict[str, Any] = {}
        for variable in variables:
            low, high = variable["minimum"], variable["maximum"]
            value = low if sampling.method == "uniform" and count == 1 else (
                low + (high - low) * index / (count - 1)
                if sampling.method == "uniform"
                else generator.uniform(low, high)
            )
            if variable["kind"] == "integer":
                value = round(value)
            row[variable["name"]] = value
        rows.append(row)
    return rows


def _study_path(workspace_id: str, study_id: str) -> Path:
    if not WORKSPACE_ID_RE.fullmatch(study_id):
        raise HTTPException(404, "Study not found")
    target = (_workspace_dir(workspace_id) / "studies" / f"{study_id}.json").resolve()
    studies_root = (_workspace_dir(workspace_id) / "studies").resolve()
    if studies_root not in target.parents or not target.is_file():
        raise HTTPException(404, "Study not found")
    return target


def _resolve_study(workspace_id: str, body: StudyRequest, study_id: str | None = None) -> dict[str, Any]:
    manifest = _workspace_manifest(workspace_id)
    project_root = _project_root(workspace_id)
    graph = referenced_files(project_root, manifest["entry"])
    if not body.name.strip():
        raise HTTPException(400, "Enter a study name")
    if not body.variables:
        raise HTTPException(400, "Add at least one variable")
    if len(body.variables) > 200:
        raise HTTPException(400, "A study supports at most 200 variables")
    allowed_files = {node["path"] for node in graph["files"]}
    names: set[str] = set()
    resolved_variables = []
    for variable in body.variables:
        name = variable.name.strip()
        if not name:
            raise HTTPException(400, "Every variable needs a name")
        if name in names:
            raise HTTPException(400, f"Duplicate variable name: {name}")
        names.add(name)
        if variable.file not in allowed_files:
            raise HTTPException(400, f"'{variable.file}' is not linked by the workspace project")
        parsed = parse_file(safe_path(project_root, variable.file))
        matches = [item for item in parsed["parameters"] if item["key"] == variable.key]
        if not matches:
            raise HTTPException(400, f"Parameter '{variable.key}' was not found in {variable.file}")
        if len(matches) > 1:
            raise HTTPException(400, f"Parameter '{variable.key}' is ambiguous in {variable.file}")
        parameter = matches[0]
        resolved_variables.append({
            "name": name,
            "file": variable.file,
            "key": variable.key,
            "original_value": parameter["value"],
            "kind": parameter["kind"],
            "description": parameter["description"],
            "minimum": variable.minimum,
            "maximum": variable.maximum,
        })
    samples = _make_samples(resolved_variables, body.sampling)
    now = _utc_now().isoformat()
    studies_dir = _workspace_dir(workspace_id) / "studies"
    studies_dir.mkdir(exist_ok=True)
    created_at = now
    if study_id:
        target = _study_path(workspace_id, study_id)
        previous = json.loads(target.read_text(encoding="utf-8"))
        created_at = previous.get("created_at", now)
    else:
        study_id = f"{_slug(body.name, 'variable-study')}-{uuid.uuid4().hex[:6]}"
        target = studies_dir / f"{study_id}.json"
    payload = {
        "schema_version": 1,
        "study_id": study_id,
        "workspace_id": workspace_id,
        "name": body.name.strip(),
        "created_at": created_at,
        "updated_at": now,
        "workspace_entry": manifest["entry"],
        "variables": resolved_variables,
        "sampling": {
            "method": body.sampling.method,
            "count": len(samples),
            "seed": body.sampling.seed if body.sampling.method == "random" else None,
            "samples": samples,
        },
    }
    _json_write(target, payload)
    return payload


def _study_summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "study_id": payload["study_id"],
        "name": payload["name"],
        "updated_at": payload["updated_at"],
        "variable_count": len(payload.get("variables", [])),
        "sample_count": payload.get("sampling", {}).get("count", 0),
        "download_url": f"/api/workspaces/{payload['workspace_id']}/studies/{payload['study_id']}/download",
    }


def _set_run(workspace_id: str, run_id: str, **updates: Any) -> None:
    with RUNS_LOCK:
        RUNS[(workspace_id, run_id)].update(updates)


def _workspace_run_lock(workspace_id: str) -> threading.Lock:
    with WORKSPACE_RUN_LOCKS_LOCK:
        return WORKSPACE_RUN_LOCKS.setdefault(workspace_id, threading.Lock())


def _workspace_has_active_run(workspace_id: str) -> bool:
    with RUNS_LOCK:
        return any(
            run_workspace_id == workspace_id and state.get("status") in {"queued", "running"}
            for (run_workspace_id, _), state in RUNS.items()
        )


def _wind_run_metadata(payload: dict[str, Any], generated: bool = False) -> dict[str, Any]:
    return {
        "mode": payload["mode"],
        "inflow_file": payload["inflow_file"],
        "file_name_bts": payload["file_name_bts"],
        "resolved_bts": payload["resolved_bts"],
        "turbsim_input": payload["selected_turbsim_input"],
        "managed_bts": payload["managed_bts"],
        "generated": generated,
        "generation_reason": "missing_or_stale" if generated else "not_required",
    }


def _execute_run(workspace_id: str, run_id: str, model_path: Path, executable: str) -> None:
    workspace_dir = _workspace_dir(workspace_id)
    results_root = workspace_dir / "results"
    run_dir = results_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "console.log"
    wind_payload: dict[str, Any] | None = None
    wind_metadata: dict[str, Any] | None = None
    generated = False
    with RUNS_LOCK:
        started_at = RUNS[(workspace_id, run_id)]["started_at"]
    try:
        with _workspace_run_lock(workspace_id):
            _set_run(workspace_id, run_id, status="running", phase="preflight")
            manifest = _workspace_manifest(workspace_id)
            turbsim_executable = find_turbsim()
            wind_payload = wind_status(
                _project_root(workspace_id),
                manifest["entry"],
                manifest,
                turbsim_executable=turbsim_executable,
            )
            if wind_payload["active"] and not wind_payload["valid"]:
                raise RuntimeError(wind_payload["message"])

            with log_path.open("a", encoding="utf-8") as log:
                def emit(message: str) -> None:
                    log.write(message)
                    log.flush()

                if wind_payload["mode"] == "managed":
                    if wind_payload["needs_generation"]:
                        if not turbsim_executable:
                            raise RuntimeError("TurbSim executable not found in the active environment or PATH")
                        input_path = project_path(
                            _project_root(workspace_id),
                            wind_payload["selected_turbsim_input"],
                        )
                        emit(f"TurbSim input: {input_path}\n")
                        emit(f"TurbSim output: {input_path.with_suffix('.bts')}\n")
                        emit(f"Command: {turbsim_executable} {input_path.name}\n\n")
                        _set_run(workspace_id, run_id, phase="turbsim")
                        return_code = run_turbsim(input_path, turbsim_executable, emit)
                        if return_code != 0:
                            raise RuntimeError(f"TurbSim failed with exit code {return_code}")
                        output_path = input_path.with_suffix(".bts")
                        if not output_path.is_file() or output_path.stat().st_size <= 0:
                            raise RuntimeError(f"TurbSim did not create the expected non-empty output: {output_path.name}")
                        generated = True
                        emit("\nTurbSim wind field generated successfully.\n\n")
                    else:
                        emit(f"Reusing current TurbSim wind field: {wind_payload['resolved_bts']}\n\n")
                elif wind_payload["mode"] == "external":
                    emit(f"Using external TurbSim wind field without generation: {wind_payload['resolved_bts']}\n\n")

            wind_metadata = _wind_run_metadata(wind_payload, generated)
            if generated and turbsim_executable:
                wind_metadata.update({
                    "turbsim_executable": turbsim_executable,
                    "turbsim_version": turbsim_version(turbsim_executable),
                    "turbsim_return_code": 0,
                })
            _set_run(workspace_id, run_id, phase="openfast")
            return_code, run_dir = run_openfast(
                model_path,
                executable,
                results_root,
                run_id,
                echo_console=False,
                manifest_metadata={
                    "workspace_id": workspace_id,
                    "workspace_entry": manifest["entry"],
                    "phase": "complete" if generated or wind_payload["valid"] else "failed",
                    "wind": wind_metadata,
                },
                reuse_run_dir=True,
                append_console=True,
            )
        manifest_path = run_dir / "manifest.json"
        if manifest_path.is_file():
            run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            run_manifest["model"] = _workspace_manifest(workspace_id)["entry"]
            run_manifest["phase"] = "complete" if return_code == 0 else "failed"
            _json_write(manifest_path, run_manifest)
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"\nmcFAST run preflight failed: {exc}\n")
        finished_at = _utc_now().isoformat()
        failed_manifest = {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "workspace_entry": _workspace_manifest(workspace_id)["entry"],
            "status": "failed",
            "phase": "failed",
            "return_code": None,
            "error": str(exc),
            "started_at": started_at,
            "finished_at": finished_at,
            "model": _workspace_manifest(workspace_id)["entry"],
            "executable": executable,
            "wind": wind_metadata or (_wind_run_metadata(wind_payload, generated) if wind_payload else None),
            "outputs": [],
        }
        _json_write(run_dir / "manifest.json", failed_manifest)
        _set_run(workspace_id, run_id, status="failed", phase="failed", error=str(exc), return_code=None)
        return
    _set_run(
        workspace_id,
        run_id,
        status="completed" if return_code == 0 else "failed",
        phase="complete" if return_code == 0 else "failed",
        return_code=return_code,
    )


def _run_dir(workspace_id: str, run_id: str) -> Path:
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(404, "Run not found")
    results_root = (_workspace_dir(workspace_id) / "results").resolve()
    target = (results_root / run_id).resolve()
    if results_root not in target.parents:
        raise HTTPException(404, "Run not found")
    return target


def _run_payload(workspace_id: str, run_id: str, offset: int = 0) -> dict[str, Any]:
    with RUNS_LOCK:
        state = dict(RUNS.get((workspace_id, run_id), {}))
    run_dir = _run_dir(workspace_id, run_id)
    manifest: dict[str, Any] | None = None
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("workspace_id") != workspace_id:
            raise HTTPException(404, "Run not found")
        state.update({
            "model": manifest.get("model"),
            "status": manifest.get("status"),
            "return_code": manifest.get("return_code"),
            "error": manifest.get("error"),
            "phase": manifest.get("phase"),
        })
    if not state:
        raise HTTPException(404, "Run not found")
    log_path = run_dir / "console.log"
    console = ""
    next_offset = 0
    if log_path.is_file():
        with log_path.open("rb") as log:
            size = log.seek(0, 2)
            if offset > size:
                offset = 0
            log.seek(offset)
            console = log.read().decode("utf-8", errors="replace")
            next_offset = log.tell()
    artifacts = []
    if run_dir.is_dir():
        for path in sorted(run_dir.iterdir()):
            if path.is_file():
                artifacts.append({
                    "name": path.name,
                    "bytes": path.stat().st_size,
                    "url": f"/api/workspaces/{workspace_id}/runs/{run_id}/files/{path.name}",
                })
    return {
        "workspace_id": workspace_id,
        "run_id": run_id,
        "model": state.get("model"),
        "status": state["status"],
        "phase": state.get("phase"),
        "return_code": state.get("return_code"),
        "error": state.get("error"),
        "console": console,
        "next_offset": next_offset,
        "artifacts": artifacts,
        "manifest": manifest,
    }


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/sources")
def sources() -> dict[str, Any]:
    return {
        "root": str(MODEL_ROOT),
        "sources": [
            {**source, "source_path": str(safe_path(MODEL_ROOT, source["path"]))}
            for source in discover_models(MODEL_ROOT)
        ],
    }


@app.get("/api/workspaces")
def workspaces() -> dict[str, Any]:
    _ensure_example_workspace()
    items = [_workspace_summary(item) for item in _list_workspace_manifests()]
    return {
        "workspaces": items,
        "onboarding": None if items else "No workspace projects found. Run `uv run python scripts/fetch_iea15mw.py` or import a local .fst path.",
    }


@app.post("/api/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceImportRequest) -> dict[str, Any]:
    return _import_workspace(body)


@app.get("/api/workspaces/{workspace_id}/model")
def workspace_model(workspace_id: str) -> dict[str, Any]:
    manifest = _workspace_manifest(workspace_id)
    project_root = _project_root(workspace_id)
    try:
        graph = referenced_files(project_root, manifest["entry"])
        graph["geometry"] = model_geometry(project_root, manifest["entry"])
        graph["workspace"] = _workspace_summary(manifest)
        return graph
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/file")
def workspace_file(workspace_id: str, path: str = Query(...)) -> dict[str, Any]:
    try:
        result = parse_file(safe_path(_project_root(workspace_id), path))
        result["path"] = path
        result["mtime_ns"] = str(result["mtime_ns"])
        return result
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc


@app.put("/api/workspaces/{workspace_id}/file")
def edit_workspace_file(workspace_id: str, body: UpdateRequest, path: str = Query(...)) -> dict[str, Any]:
    try:
        result = update_file(safe_path(_project_root(workspace_id), path), body.updates, body.expected_mtime_ns)
        result["path"] = path
        result["mtime_ns"] = str(result["mtime_ns"])
        manifest = _workspace_manifest(workspace_id)
        manifest["updated_at"] = _utc_now().isoformat()
        _json_write(_workspace_dir(workspace_id) / "workspace.json", manifest)
        return result
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/wind")
def workspace_wind(workspace_id: str) -> dict[str, Any]:
    manifest = _workspace_manifest(workspace_id)
    try:
        return wind_status(
            _project_root(workspace_id),
            manifest["entry"],
            manifest,
            turbsim_executable=find_turbsim(),
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/workspaces/{workspace_id}/wind")
def configure_workspace_wind(workspace_id: str, body: WindConfigurationRequest) -> dict[str, Any]:
    if _workspace_has_active_run(workspace_id):
        raise HTTPException(409, "Wait for the active workspace run before changing its TurbSim input")
    manifest = _workspace_manifest(workspace_id)
    project_root = _project_root(workspace_id)
    found = find_inflow_file(project_root, manifest["entry"])
    if not found:
        raise HTTPException(400, "The workspace deck does not expose a linked InflowWind input")
    inflow_relative, _ = found
    try:
        input_path, reference = managed_bts_reference(project_root, inflow_relative, body.turbsim_input)
        update_file(
            safe_path(project_root, inflow_relative),
            {"FileName_BTS": reference},
            body.expected_inflow_mtime_ns,
        )
        manifest["wind"] = {"turbsim_input": input_path.relative_to(project_root.resolve()).as_posix()}
        manifest["updated_at"] = _utc_now().isoformat()
        _json_write(_workspace_dir(workspace_id) / "workspace.json", manifest)
        return wind_status(
            project_root,
            manifest["entry"],
            manifest,
            turbsim_executable=find_turbsim(),
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, FileNotFoundError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/studies")
def studies(workspace_id: str) -> dict[str, Any]:
    studies_dir = _workspace_dir(workspace_id) / "studies"
    items = []
    if studies_dir.is_dir():
        for path in studies_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if payload.get("workspace_id") == workspace_id:
                    items.append(_study_summary(payload))
            except (OSError, json.JSONDecodeError, KeyError):
                continue
    items.sort(key=lambda item: item["updated_at"], reverse=True)
    return {"studies": items}


@app.post("/api/workspaces/{workspace_id}/studies", status_code=status.HTTP_201_CREATED)
def create_study(workspace_id: str, body: StudyRequest) -> dict[str, Any]:
    return _study_summary(_resolve_study(workspace_id, body))


@app.get("/api/workspaces/{workspace_id}/studies/{study_id}")
def study(workspace_id: str, study_id: str) -> dict[str, Any]:
    return json.loads(_study_path(workspace_id, study_id).read_text(encoding="utf-8"))


@app.put("/api/workspaces/{workspace_id}/studies/{study_id}")
def update_study(workspace_id: str, study_id: str, body: StudyRequest) -> dict[str, Any]:
    return _study_summary(_resolve_study(workspace_id, body, study_id))


@app.get("/api/workspaces/{workspace_id}/studies/{study_id}/download")
def download_study(workspace_id: str, study_id: str) -> FileResponse:
    target = _study_path(workspace_id, study_id)
    return FileResponse(target, filename=f"{study_id}.json")


@app.get("/api/workspaces/{workspace_id}/runs")
def workspace_runs(workspace_id: str) -> dict[str, Any]:
    results_root = _workspace_dir(workspace_id) / "results"
    items: dict[str, dict[str, Any]] = {}
    if results_root.is_dir():
        for manifest_path in results_root.glob("*/manifest.json"):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                if payload.get("workspace_id") != workspace_id:
                    continue
                items[payload["run_id"]] = {
                    "run_id": payload["run_id"],
                    "status": payload["status"],
                    "return_code": payload.get("return_code"),
                    "started_at": payload.get("started_at"),
                    "finished_at": payload.get("finished_at"),
                    "model": payload.get("model"),
                    "phase": payload.get("phase"),
                }
            except (OSError, json.JSONDecodeError, KeyError):
                continue
    with RUNS_LOCK:
        for (run_workspace_id, run_id), state in RUNS.items():
            if run_workspace_id == workspace_id and run_id not in items:
                items[run_id] = {"run_id": run_id, **state}
    return {"runs": sorted(items.values(), key=lambda item: item.get("started_at") or "", reverse=True)}


@app.post("/api/workspaces/{workspace_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def start_workspace_run(workspace_id: str) -> dict[str, Any]:
    manifest = _workspace_manifest(workspace_id)
    model_path = safe_path(_project_root(workspace_id), manifest["entry"])
    executable = find_openfast()
    if not executable:
        raise HTTPException(503, "OpenFAST executable not found in the active environment or PATH")
    wind = wind_status(
        _project_root(workspace_id),
        manifest["entry"],
        manifest,
        turbsim_executable=find_turbsim(),
    )
    if wind["active"] and not wind["valid"]:
        unavailable = wind["mode"] == "managed" and wind["needs_generation"] and not wind["turbsim_available"]
        raise HTTPException(503 if unavailable else 409, wind["message"])
    run_id = f"{model_path.stem}-{uuid.uuid4().hex[:8]}"
    (_workspace_dir(workspace_id) / "results").mkdir(exist_ok=True)
    with RUNS_LOCK:
        RUNS[(workspace_id, run_id)] = {
            "workspace_id": workspace_id,
            "model": manifest["entry"],
            "status": "queued",
            "phase": "queued",
            "return_code": None,
            "error": None,
            "started_at": _utc_now().isoformat(),
        }
    threading.Thread(
        target=_execute_run,
        args=(workspace_id, run_id, model_path, executable),
        name=f"mcfast-run-{run_id}",
        daemon=True,
    ).start()
    return _run_payload(workspace_id, run_id)


@app.get("/api/workspaces/{workspace_id}/runs/{run_id}")
def workspace_run_status(workspace_id: str, run_id: str, offset: int = Query(0, ge=0)) -> dict[str, Any]:
    return _run_payload(workspace_id, run_id, offset)


@app.get("/api/workspaces/{workspace_id}/runs/{run_id}/files/{filename:path}")
def workspace_run_artifact(workspace_id: str, run_id: str, filename: str) -> FileResponse:
    run_dir = _run_dir(workspace_id, run_id)
    target = (run_dir / filename).resolve()
    if run_dir not in target.parents or not target.is_file():
        raise HTTPException(404, "Artifact not found")
    return FileResponse(target, filename=target.name)


WEB_DIST = PROJECT_ROOT / "web" / "dist"
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def frontend(path: str = "") -> FileResponse:
        requested = WEB_DIST / path
        return FileResponse(requested if requested.is_file() else WEB_DIST / "index.html")
