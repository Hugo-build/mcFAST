import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from mcfast import api


def _source_project(root: Path, name: str = "Example") -> Path:
    case = root / "case"
    shared = root / "shared"
    case.mkdir(parents=True)
    shared.mkdir()
    entry = case / f"{name}.fst"
    entry.write_text('"../shared/AeroDyn.dat" AeroFile - linked module\n10.0 TMax - duration\n')
    (shared / "AeroDyn.dat").write_text(
        "10.0 AirDens - air density\n"
        "2 NumBlades - blade count\n"
        "True UseTipLoss - tip-loss switch\n"
        '"steady" OperationMode - operating mode\n'
        "default WakeMod - keyword sentinel\n"
    )
    return entry


def _wind_source_project(root: Path) -> Path:
    case = root / "case"
    shared = root / "shared"
    wind = shared / "Wind"
    case.mkdir(parents=True)
    wind.mkdir(parents=True)
    entry = case / "WindCase.fst"
    entry.write_text('"../shared/Inflow.dat" InflowFile - inflow input\n1 CompInflow - use inflow\n')
    (shared / "Inflow.dat").write_text(
        "3 WindType - binary TurbSim full field\n"
        '"none" FileName_BTS - full-field wind file\n'
    )
    (wind / "Case.in").write_text(
        "True WrADFF - write AeroDyn full-field output\n"
        "10.0 URef - reference wind speed\n"
    )
    return entry


def _wait_for_run(client: TestClient, base: str, run_id: str) -> dict:
    payload = client.get(f"{base}/{run_id}").json()
    deadline = time.monotonic() + 3
    while payload["status"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.01)
        payload = client.get(f"{base}/{run_id}").json()
    return payload


def _import(client: TestClient, source: Path, name: str = "Baseline") -> dict:
    response = client.post("/api/workspaces", json={"name": name, "source_path": str(source)})
    assert response.status_code == 201, response.text
    return response.json()


def _study_payload(name: str = "Blade sweep") -> dict:
    return {
        "name": name,
        "variables": [{
            "name": "blade_count",
            "file": "source/shared/AeroDyn.dat",
            "key": "NumBlades",
        }],
        "samples": [{"blade_count": 2}, {"blade_count": 3}, {"blade_count": 4}],
    }


def test_import_mirrors_common_tree_and_edits_only_workspace(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source = _source_project(source_root)
    original_source = source.read_bytes()
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        created = _import(client, source)
        workspace_id = created["workspace_id"]
        workspace_dir = api.WORKSPACE_ROOT / workspace_id
        manifest = json.loads((workspace_dir / "workspace.json").read_text())
        assert manifest["entry"] == "source/case/Example.fst"
        assert (workspace_dir / "project/source/shared/AeroDyn.dat").is_file()

        graph = client.get(f"/api/workspaces/{workspace_id}/model")
        assert graph.status_code == 200
        assert {item["name"] for item in graph.json()["files"]} == {"Example.fst", "AeroDyn.dat"}

        file_path = "source/case/Example.fst"
        before = client.get(f"/api/workspaces/{workspace_id}/file", params={"path": file_path}).json()
        edited = client.put(
            f"/api/workspaces/{workspace_id}/file",
            params={"path": file_path},
            json={"updates": {"TMax": 20}, "expected_mtime_ns": before["mtime_ns"]},
        )
        assert edited.status_code == 200
        assert edited.json()["data"]["TMax"] == 20
        assert source.read_bytes() == original_source


def test_import_rejects_invalid_duplicate_symlink_and_cleans_failed_copy(monkeypatch, tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source = _source_project(source_root)
    workspace_root = tmp_path / "workspaces"
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", workspace_root)

    with TestClient(api.app) as client:
        assert client.post("/api/workspaces", json={"name": "Bad", "source_path": str(tmp_path / "missing.fst")}).status_code == 404
        _import(client, source, "Unique")
        assert client.post("/api/workspaces", json={"name": "unique", "source_path": str(source)}).status_code == 409

        linked = tmp_path / "linked.fst"
        linked.symlink_to(source)
        assert client.post("/api/workspaces", json={"name": "Link", "source_path": str(linked)}).status_code == 400

        original_copytree = api.shutil.copytree
        monkeypatch.setattr(api.shutil, "copytree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))
        failed = client.post("/api/workspaces", json={"name": "Interrupted", "source_path": str(source)})
        assert failed.status_code == 500
        assert not any(path.name.startswith(".interrupted-") for path in workspace_root.iterdir())
        monkeypatch.setattr(api.shutil, "copytree", original_copytree)


def test_import_rebases_external_native_library(monkeypatch, tmp_path: Path) -> None:
    project = tmp_path / "source" / "project"
    runtime = tmp_path / "source" / "runtime"
    project.mkdir(parents=True)
    runtime.mkdir()
    library = runtime / "libcontroller.so"
    library.write_bytes(b"binary")
    source = project / "Example.fst"
    source.write_text('"../../source/runtime/libcontroller.so" DLL_FileName - controller\n')
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        created = _import(client, source)
        assert created["external_dependencies"][0]["path"] == str(library)
        copied = api.WORKSPACE_ROOT / created["workspace_id"] / "project/project/Example.fst"
        assert str(library) in copied.read_text()
        assert source.read_text().startswith('"../../source/runtime')


def test_first_run_seeds_umaine_once(monkeypatch, tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    source = model_root / "IEA-15-240-RWT" / "case" / "IEA-15-240-RWT-UMaineSemi.fst"
    source.parent.mkdir(parents=True)
    source.write_text("10.0 TMax - duration\n")
    monkeypatch.setattr(api, "MODEL_ROOT", model_root)
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        first = client.get("/api/workspaces").json()
        second = client.get("/api/workspaces").json()
        assert [item["name"] for item in first["workspaces"]] == ["IEA 15 MW UMaineSemi"]
        assert len(second["workspaces"]) == 1


def test_multiple_studies_can_be_reopened_and_updated(monkeypatch, tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        workspace = _import(client, source)
        base = f"/api/workspaces/{workspace['workspace_id']}/studies"
        first = client.post(base, json=_study_payload()).json()
        second = client.post(base, json=_study_payload("Second sweep")).json()
        assert len(client.get(base).json()["studies"]) == 2
        reopened = client.get(f"{base}/{first['study_id']}").json()
        assert reopened["schema_version"] == 2
        assert reopened["samples"] == [
            {"blade_count": 2}, {"blade_count": 3}, {"blade_count": 4}
        ]
        replacement = _study_payload("Updated sweep")
        replacement["samples"] = [{"blade_count": 2}, {"blade_count": 4}]
        updated = client.put(f"{base}/{first['study_id']}", json=replacement)
        assert updated.status_code == 200
        assert updated.json()["sample_count"] == 2
        assert client.get(second["download_url"]).status_code == 200


def test_study_accepts_mixed_types_and_rejects_invalid_cases(monkeypatch, tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        workspace = _import(client, source)
        base = f"/api/workspaces/{workspace['workspace_id']}/studies"
        variables = [
            {"name": "density", "file": "source/shared/AeroDyn.dat", "key": "AirDens"},
            {"name": "blades", "file": "source/shared/AeroDyn.dat", "key": "NumBlades"},
            {"name": "tip_loss", "file": "source/shared/AeroDyn.dat", "key": "UseTipLoss"},
            {"name": "mode", "file": "source/shared/AeroDyn.dat", "key": "OperationMode"},
        ]
        created = client.post(base, json={
            "name": "Mixed values",
            "variables": variables,
            "samples": [{"density": "1.225", "blades": "3", "tip_loss": "false", "mode": "parked"}],
        })
        assert created.status_code == 201, created.text
        saved = client.get(f"{base}/{created.json()['study_id']}").json()
        assert saved["samples"] == [{"density": 1.225, "blades": 3, "tip_loss": False, "mode": "parked"}]

        blank = client.post(base, json={"name": "Blank", "variables": variables, "samples": [
            {"density": 1.0, "blades": 3, "tip_loss": True, "mode": ""}
        ]})
        assert blank.status_code == 400
        assert "case row 1" in blank.json()["detail"]

        non_finite = client.post(base, json={"name": "NaN", "variables": variables[:1], "samples": [{"density": "nan"}]})
        assert non_finite.status_code == 400
        duplicate = client.post(base, json={
            "name": "Duplicate", "variables": [variables[0], {**variables[1], "name": "density"}],
            "samples": [{"density": 1}],
        })
        assert duplicate.status_code == 400


def test_csv_import_appends_only_valid_typed_rows(monkeypatch, tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        workspace = _import(client, source)
        url = f"/api/workspaces/{workspace['workspace_id']}/studies/csv-import"
        variables = [
            {"name": "blade_count", "file": "source/shared/AeroDyn.dat", "key": "NumBlades"},
            {"name": "mode", "file": "source/shared/AeroDyn.dat", "key": "OperationMode"},
        ]
        imported = client.post(url, json={
            "variables": variables,
            "csv_text": 'blade_count,mode,ignored\n3,"parked, safe",x\n2.5,bad,x\n4,,x\n\n',
        })
        assert imported.status_code == 200, imported.text
        assert imported.json()["samples"] == [{"blade_count": 3, "mode": "parked, safe"}]
        assert imported.json()["imported_count"] == 1
        assert imported.json()["skipped_count"] == 2
        assert imported.json()["errors"][0]["row"] == 3

        missing = client.post(url, json={"variables": variables, "csv_text": "blade_count\n3\n"})
        assert missing.status_code == 400
        assert "missing column(s): mode" in missing.json()["detail"]

        many_errors = client.post(url, json={
            "variables": variables,
            "csv_text": "blade_count,mode\n" + "\n".join("invalid,ok" for _ in range(25)),
        }).json()
        assert many_errors["skipped_count"] == 25
        assert len(many_errors["errors"]) == 20


def test_legacy_study_is_normalized_without_rewriting(monkeypatch, tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        workspace = _import(client, source)
        workspace_id = workspace["workspace_id"]
        study_id = "legacy-study"
        study_path = api.WORKSPACE_ROOT / workspace_id / "studies" / f"{study_id}.json"
        legacy = {
            "schema_version": 1,
            "study_id": study_id,
            "workspace_id": workspace_id,
            "name": "Legacy sweep",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
            "workspace_entry": workspace["entry"],
            "variables": [{
                "name": "blade_count", "file": "source/shared/AeroDyn.dat", "key": "NumBlades",
                "kind": "integer", "original_value": 2, "description": "blade count", "minimum": 2, "maximum": 4,
            }],
            "sampling": {"method": "uniform", "count": 3, "seed": None, "samples": [
                {"blade_count": 2}, {"blade_count": 3}, {"blade_count": 4},
            ]},
        }
        study_path.write_text(json.dumps(legacy))
        base = f"/api/workspaces/{workspace_id}/studies"

        assert client.get(base).json()["studies"][0]["sample_count"] == 3
        normalized = client.get(f"{base}/{study_id}").json()
        assert normalized["schema_version"] == 2
        assert "sampling" not in normalized
        assert "minimum" not in normalized["variables"][0]
        assert normalized["samples"] == legacy["sampling"]["samples"]
        assert json.loads(study_path.read_text())["schema_version"] == 1
        downloaded = client.get(f"{base}/{study_id}/download").json()
        assert downloaded["schema_version"] == 2
        assert downloaded["samples"] == legacy["sampling"]["samples"]


def test_workspace_run_history_survives_memory_reset(monkeypatch, tmp_path: Path) -> None:
    source = _source_project(tmp_path / "source")
    executable = tmp_path / "openfast"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-v\" ]; then echo 'OpenFAST-v4.2.1'; exit 0; fi\n"
        "echo 'Time: 1 of 1 seconds.'\n"
        "stem=${1%.fst}\n"
        "echo result > \"${stem}.outb\"\n"
    )
    executable.chmod(0o755)
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api, "find_openfast", lambda: str(executable))
    api.RUNS.clear()

    with TestClient(api.app) as client:
        workspace = _import(client, source)
        base = f"/api/workspaces/{workspace['workspace_id']}/runs"
        started = client.post(base)
        assert started.status_code == 202
        run_id = started.json()["run_id"]
        payload = started.json()
        deadline = time.monotonic() + 2
        while payload["status"] in {"queued", "running"} and time.monotonic() < deadline:
            time.sleep(0.01)
            payload = client.get(f"{base}/{run_id}").json()
        assert payload["status"] == "completed"
        api.RUNS.clear()
        history = client.get(base).json()["runs"]
        assert history[0]["run_id"] == run_id
        persisted = client.get(f"{base}/{run_id}").json()
        assert "Time: 1 of 1 seconds." in persisted["console"]
        artifact = next(item for item in persisted["artifacts"] if item["name"] == "Example.outb")
        assert client.get(artifact["url"]).content == b"result\n"


def test_workspace_boundaries_reject_cross_project_paths(monkeypatch, tmp_path: Path) -> None:
    source_one = _source_project(tmp_path / "one", "One")
    source_two = _source_project(tmp_path / "two", "Two")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")

    with TestClient(api.app) as client:
        first = _import(client, source_one, "One")
        second = _import(client, source_two, "Two")
        traversal = client.get(
            f"/api/workspaces/{first['workspace_id']}/file",
            params={"path": f"../../{second['workspace_id']}/workspace.json"},
        )
        assert traversal.status_code == 404


def test_wind_configuration_discovers_input_and_classifies_manual_bts_as_external(
    monkeypatch, tmp_path: Path
) -> None:
    source = _wind_source_project(tmp_path / "source")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api, "find_turbsim", lambda: "/runtime/turbsim")

    with TestClient(api.app) as client:
        workspace = _import(client, source, "Wind workspace")
        base = f"/api/workspaces/{workspace['workspace_id']}"
        initial = client.get(f"{base}/wind").json()
        assert initial["mode"] == "unconfigured"
        assert initial["turbsim_inputs"][0]["path"] == "source/shared/Wind/Case.in"
        model = client.get(f"{base}/model").json()
        turbsim_node = next(item for item in model["files"] if item["path"] == "source/shared/Wind/Case.in")
        assert turbsim_node["source_kind"] == "turbsim"
        assert turbsim_node["parameter_count"] == 2

        study = client.post(f"{base}/studies", json={
            "name": "TurbSim wind speeds",
            "variables": [{
                "name": "reference_wind",
                "file": "source/shared/Wind/Case.in",
                "key": "URef",
            }],
            "samples": [{"reference_wind": 8}, {"reference_wind": 12.5}],
        })
        assert study.status_code == 201, study.text
        saved_study = client.get(f"{base}/studies/{study.json()['study_id']}").json()
        assert saved_study["variables"][0]["file"] == "source/shared/Wind/Case.in"
        assert saved_study["samples"] == [{"reference_wind": 8.0}, {"reference_wind": 12.5}]

        configured = client.put(
            f"{base}/wind",
            json={
                "turbsim_input": "source/shared/Wind/Case.in",
                "expected_inflow_mtime_ns": initial["inflow_mtime_ns"],
            },
        )
        assert configured.status_code == 200, configured.text
        assert configured.json()["mode"] == "managed"
        assert configured.json()["file_name_bts"] == "Wind/Case.bts"
        manifest = json.loads((api.WORKSPACE_ROOT / workspace["workspace_id"] / "workspace.json").read_text())
        assert manifest["wind"]["turbsim_input"] == "source/shared/Wind/Case.in"

        project = api.WORKSPACE_ROOT / workspace["workspace_id"] / "project/source"
        (project / "shared/Wind/external.bts").write_bytes(b"existing wind")
        inflow_path = "source/shared/Inflow.dat"
        inflow = client.get(f"{base}/file", params={"path": inflow_path}).json()
        edited = client.put(
            f"{base}/file",
            params={"path": inflow_path},
            json={"updates": {"FileName_BTS": "Wind/external.bts"}, "expected_mtime_ns": inflow["mtime_ns"]},
        )
        assert edited.status_code == 200
        external = client.get(f"{base}/wind").json()
        assert external["mode"] == "external"
        assert external["valid"] is True
        assert external["needs_generation"] is False

        escaped = client.put(f"{base}/wind", json={"turbsim_input": "../../outside.in"})
        assert escaped.status_code == 400


def test_managed_wind_generates_then_reuses_current_bts(monkeypatch, tmp_path: Path) -> None:
    source = _wind_source_project(tmp_path / "source")
    openfast = tmp_path / "openfast"
    openfast.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-v\" ]; then echo 'OpenFAST-v4.2.1'; exit 0; fi\n"
        "echo 'Time: 1 of 1 seconds.'\n"
        "stem=${1%.fst}\n"
        "echo result > \"${stem}.outb\"\n"
    )
    openfast.chmod(0o755)
    turbsim = tmp_path / "turbsim"
    turbsim.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-v\" ]; then echo 'TurbSim-v4.2.1'; exit 0; fi\n"
        "echo 'turbulence generated'\n"
        "stem=${1%.in}\n"
        "echo wind > \"${stem}.bts\"\n"
    )
    turbsim.chmod(0o755)
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api, "find_openfast", lambda: str(openfast))
    monkeypatch.setattr(api, "find_turbsim", lambda: str(turbsim))
    api.RUNS.clear()

    with TestClient(api.app) as client:
        workspace = _import(client, source, "Managed wind")
        base = f"/api/workspaces/{workspace['workspace_id']}"
        status_payload = client.get(f"{base}/wind").json()
        client.put(
            f"{base}/wind",
            json={
                "turbsim_input": "source/shared/Wind/Case.in",
                "expected_inflow_mtime_ns": status_payload["inflow_mtime_ns"],
            },
        )

        runs = f"{base}/runs"
        first = client.post(runs)
        assert first.status_code == 202, first.text
        first_result = _wait_for_run(client, runs, first.json()["run_id"])
        assert first_result["status"] == "completed"
        assert "turbulence generated" in first_result["console"]
        assert first_result["manifest"]["wind"]["generated"] is True
        assert first_result["manifest"]["wind"]["turbsim_version"] == "TurbSim-v4.2.1"

        second = client.post(runs)
        assert second.status_code == 202
        second_result = _wait_for_run(client, runs, second.json()["run_id"])
        assert second_result["status"] == "completed"
        assert "Reusing current TurbSim wind field" in second_result["console"]
        assert second_result["manifest"]["wind"]["generated"] is False

        project = api.WORKSPACE_ROOT / workspace["workspace_id"] / "project/source"
        input_path = project / "shared/Wind/Case.in"
        output_path = input_path.with_suffix(".bts")
        os.utime(input_path, ns=(output_path.stat().st_mtime_ns + 1_000_000_000,) * 2)
        stale = client.post(runs)
        stale_result = _wait_for_run(client, runs, stale.json()["run_id"])
        assert stale_result["status"] == "completed"
        assert stale_result["manifest"]["wind"]["generated"] is True


def test_external_bts_runs_openfast_without_invoking_turbsim(monkeypatch, tmp_path: Path) -> None:
    source = _wind_source_project(tmp_path / "source")
    openfast = tmp_path / "openfast"
    openfast.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-v\" ]; then echo 'OpenFAST-v4.2.1'; exit 0; fi\n"
        "stem=${1%.fst}\n"
        "echo result > \"${stem}.outb\"\n"
    )
    openfast.chmod(0o755)
    turbsim = tmp_path / "turbsim"
    turbsim.write_text("#!/bin/sh\ntouch .turbsim-called\n")
    turbsim.chmod(0o755)
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api, "find_openfast", lambda: str(openfast))
    monkeypatch.setattr(api, "find_turbsim", lambda: str(turbsim))
    api.RUNS.clear()

    with TestClient(api.app) as client:
        workspace = _import(client, source, "External wind run")
        base = f"/api/workspaces/{workspace['workspace_id']}"
        project = api.WORKSPACE_ROOT / workspace["workspace_id"] / "project/source"
        (project / "shared/Wind/external.bts").write_bytes(b"existing wind")
        inflow_path = "source/shared/Inflow.dat"
        inflow = client.get(f"{base}/file", params={"path": inflow_path}).json()
        client.put(
            f"{base}/file",
            params={"path": inflow_path},
            json={"updates": {"FileName_BTS": "Wind/external.bts"}, "expected_mtime_ns": inflow["mtime_ns"]},
        )

        started = client.post(f"{base}/runs")
        result = _wait_for_run(client, f"{base}/runs", started.json()["run_id"])
        assert result["status"] == "completed"
        assert "without generation" in result["console"]
        assert result["manifest"]["wind"]["mode"] == "external"
        assert result["manifest"]["wind"]["generated"] is False
        assert not (project / "shared/Wind/.turbsim-called").exists()


def test_mode_three_preflight_rejects_invalid_managed_or_missing_external_wind(
    monkeypatch, tmp_path: Path
) -> None:
    source = _wind_source_project(tmp_path / "source")
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api, "find_openfast", lambda: "/runtime/openfast")
    monkeypatch.setattr(api, "find_turbsim", lambda: "/runtime/turbsim")

    with TestClient(api.app) as client:
        workspace = _import(client, source, "Invalid wind")
        base = f"/api/workspaces/{workspace['workspace_id']}"
        wind = client.get(f"{base}/wind").json()
        assert client.post(f"{base}/runs").status_code == 409
        client.put(
            f"{base}/wind",
            json={
                "turbsim_input": "source/shared/Wind/Case.in",
                "expected_inflow_mtime_ns": wind["inflow_mtime_ns"],
            },
        )
        monkeypatch.setattr(api, "find_turbsim", lambda: None)
        assert client.post(f"{base}/runs").status_code == 503
        monkeypatch.setattr(api, "find_turbsim", lambda: "/runtime/turbsim")
        turbsim_input = "source/shared/Wind/Case.in"
        parsed = client.get(f"{base}/file", params={"path": turbsim_input}).json()
        client.put(
            f"{base}/file",
            params={"path": turbsim_input},
            json={"updates": {"WrADFF": False}, "expected_mtime_ns": parsed["mtime_ns"]},
        )
        rejected = client.post(f"{base}/runs")
        assert rejected.status_code == 409
        assert "WrADFF" in rejected.json()["detail"]


@pytest.mark.parametrize(
    ("turbsim_body", "expected_error"),
    [
        ("echo failed; exit 2", "exit code 2"),
        ("echo succeeded-without-output; exit 0", "did not create the expected"),
    ],
)
def test_turbsim_failure_prevents_openfast(
    monkeypatch, tmp_path: Path, turbsim_body: str, expected_error: str
) -> None:
    source = _wind_source_project(tmp_path / "source")
    openfast = tmp_path / "openfast"
    openfast.write_text("#!/bin/sh\ntouch .openfast-called\n")
    openfast.chmod(0o755)
    turbsim = tmp_path / "turbsim"
    turbsim.write_text(f"#!/bin/sh\n{turbsim_body}\n")
    turbsim.chmod(0o755)
    monkeypatch.setattr(api, "MODEL_ROOT", tmp_path / "models")
    monkeypatch.setattr(api, "WORKSPACE_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(api, "find_openfast", lambda: str(openfast))
    monkeypatch.setattr(api, "find_turbsim", lambda: str(turbsim))
    api.RUNS.clear()

    with TestClient(api.app) as client:
        workspace = _import(client, source, f"Failure {expected_error}")
        base = f"/api/workspaces/{workspace['workspace_id']}"
        wind = client.get(f"{base}/wind").json()
        client.put(
            f"{base}/wind",
            json={
                "turbsim_input": "source/shared/Wind/Case.in",
                "expected_inflow_mtime_ns": wind["inflow_mtime_ns"],
            },
        )
        started = client.post(f"{base}/runs")
        result = _wait_for_run(client, f"{base}/runs", started.json()["run_id"])
        assert result["status"] == "failed"
        assert expected_error in result["error"]
        project = api.WORKSPACE_ROOT / workspace["workspace_id"] / "project/source"
        assert not (project / "case/.openfast-called").exists()
