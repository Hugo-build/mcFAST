import csv
import json
import struct

import numpy as np
import pytest

from mcfast.extract import extract_results, export_reports, main


def write_case(root, name="case-1", *, binary=False, manifest=True,
               mx=(100, -3, 0, -3), my=(0, 0, 4, 0), units=("kN-m", "kN-m")):
    case = root / name
    case.mkdir(parents=True, exist_ok=True)
    if manifest:
        (case / "manifest.json").write_text(json.dumps({"model": "remote/Example.fst", "run_id": name,
            "study_id": "original-study", "sample_index": 7, "status": "completed",
            "sample_values": {"URef": 5.3, "WaveHs": 2, "WaveTp": 12}}))
    names = ["Time", "TwrBsMxt", "TwrBsMyt", "GenPwr"]
    unit_list = ["s", *units, "kW"]
    times = [399, 400, 401, 402]
    matrix = np.array(list(zip(times, mx, my, (1, 2, 3, 4))), dtype=float)
    path = case / ("Example.outb" if binary else "Example.out")
    if binary:
        with path.open("wb") as stream:
            stream.write(struct.pack("<hii", 3, 3, 4))
            stream.write(struct.pack("<dd", 399, 1))
            stream.write(struct.pack("<i", 0))
            for fields in (names, [f"({unit})" for unit in unit_list]):
                for field in fields:
                    stream.write(field.encode().ljust(10, b" "))
            stream.write(matrix[:, 1:].astype("<f8").tobytes())
    else:
        path.write_text("\n".join(["header"] * 6 + [" ".join(names), " ".join(f"({u})" for u in unit_list)])
                        + "\n" + "\n".join(" ".join(str(v) for v in row) for row in matrix) + "\n")
    return case, path


@pytest.mark.parametrize("binary", [False, True])
def test_metrics_window_inputs_and_resultant(tmp_path, binary):
    write_case(tmp_path, "results/run", binary=binary)
    report = extract_results(tmp_path, channels=["GenPwr", "GenPwr"])
    assert report["counts"] == {"successful": 1, "partial": 0, "unavailable": 0}
    record = report["records"][0]
    assert record["case_id"] == "results/run"
    assert record["study_id"] == "original-study" and record["sample_index"] == 7
    assert record["inputs"] == {"URef": 5.3, "WaveHs": 2, "WaveTp": 12}
    assert record["interval"]["analysis_start"] == 400
    mx = record["metrics"]["TwrBsMxt"]
    assert (mx["min"], mx["min_time"], mx["max"], mx["max_time"], mx["abs_max_time"]) == (-3, 400, 0, 401, 400)
    resultant = record["metrics"]["tower_base_bending_resultant"]
    assert resultant["max"] == 4 and resultant["max_time"] == 401  # Not sqrt(3²+4²).
    power = record["metrics"]["GenPwr"]
    assert power["mean"] == pytest.approx(3) and power["std"] == pytest.approx(np.std([2, 3, 4]))
    assert power["unit"] == "kW"
    narrow = extract_results(tmp_path, start=400, end=400)["records"][0]
    assert narrow["interval"]["sample_count"] == 1
    assert narrow["metrics"]["tower_base_bending_resultant"]["max"] == 3


def test_nonfinite_and_units(tmp_path):
    write_case(tmp_path, mx=(0, float("nan"), 3, 0), my=(0, 4, 4, float("inf")))
    record = extract_results(tmp_path)["records"][0]
    assert record["extraction_status"] == "partial"
    assert record["metrics"]["TwrBsMxt"]["excluded_count"] == 1
    result = record["metrics"]["tower_base_bending_resultant"]
    assert (result["max"], result["max_time"], result["excluded_count"]) == (5, 401, 2)
    write_case(tmp_path, units=("N-m", "kN-m"))
    record = extract_results(tmp_path)["records"][0]
    assert "tower_base_bending_resultant" not in record["metrics"]
    assert "matching" in " ".join(record["diagnostics"])


def test_missing_channels_and_all_nonfinite(tmp_path):
    _, path = write_case(tmp_path, mx=(0, float("nan"), float("nan"), float("nan")))
    path.write_text(path.read_text().replace("TwrBsMyt", "Other"))
    record = extract_results(tmp_path, channels=["Missing"])["records"][0]
    assert record["extraction_status"] == "unavailable"
    assert record["metrics"]["TwrBsMxt"]["valid_count"] == 0
    assert "max" not in record["metrics"]["TwrBsMxt"]
    assert any("Missing channel: Missing" in item for item in record["diagnostics"])


def test_discovery_incomplete_and_invalid_manifest(tmp_path):
    case, _ = write_case(tmp_path, "results/case-1", manifest=False)
    write_case(tmp_path, "results/case-2")
    (tmp_path / "results/case-2/manifest.json").write_text("broken")
    empty = tmp_path / "results/slurm-case-3"
    empty.mkdir()
    module = tmp_path / "results/incomplete"
    module.mkdir()
    (module / "Example.MD.out").write_text("module")
    (tmp_path / "study.json").write_text(json.dumps({"samples": [{"URef": 999}]}))
    report = extract_results(tmp_path)
    assert len(report["records"]) == 4
    assert report["counts"] == {"successful": 0, "partial": 2, "unavailable": 2}
    assert all(record["inputs"] == {} for record in report["records"])
    assert next(r for r in report["records"] if r["case_path"] == str(case))["simulation_status"] == "unknown"


def test_preference_ambiguity_and_corrupt_output(tmp_path):
    case, text = write_case(tmp_path, manifest=False)
    _, binary = write_case(tmp_path, binary=True, manifest=False)
    record = extract_results(tmp_path)["records"][0]
    assert record["output_source"] == str(binary)
    (case / "Other.out").write_text(text.read_text())
    record = extract_results(tmp_path)["records"][0]
    assert record["extraction_status"] == "unavailable"
    assert "Ambiguous" in " ".join(record["diagnostics"])
    write_case(tmp_path)
    binary.write_bytes(b"broken")
    record = extract_results(tmp_path)["records"][0]
    assert record["extraction_status"] == "unavailable"  # Do not silently switch to text.
    assert "Cannot extract" in " ".join(record["diagnostics"])


@pytest.mark.parametrize("start,end", [(float("nan"), None), (0, float("inf")), (500, 400)])
def test_invalid_interval(tmp_path, start, end):
    with pytest.raises(ValueError, match="finite"):
        extract_results(tmp_path, start=start, end=end)


def test_empty_window_and_exports(tmp_path):
    root = tmp_path / "results"
    write_case(root)
    case, _ = write_case(root, "case-2")
    manifest = json.loads((case / "manifest.json").read_text())
    manifest["sample_values"] = {"DifferentVariable": 9}
    manifest["status"] = "failed"
    (case / "manifest.json").write_text(json.dumps(manifest))
    empty = extract_results(root, start=403)
    assert empty["counts"]["unavailable"] == 2
    report = extract_results(root)
    csv_path, json_path = export_reports(report, tmp_path / "report")
    assert json.loads(json_path.read_text()) == report
    with csv_path.open() as stream:
        rows = list(csv.DictReader(stream))
    assert rows[0]["input.URef"] == "5.3" and rows[0]["input.DifferentVariable"] == ""
    assert rows[1]["input.URef"] == "" and rows[1]["simulation_status"] == "failed"
    original = csv_path.read_bytes()
    with pytest.raises(FileExistsError):
        export_reports(empty, tmp_path / "report")
    assert csv_path.read_bytes() == original
    export_reports(empty, tmp_path / "report", overwrite=True)
    assert json.loads(json_path.read_text())["counts"]["unavailable"] == 2


def test_cli_and_existing_single_report(tmp_path, monkeypatch, capsys):
    root, out = tmp_path / "results", tmp_path / "report"
    write_case(root)
    monkeypatch.setattr("sys.argv", ["mcfast-extract", str(root), "--output", str(out), "--channel", "GenPwr"])
    main()
    assert "successful: 1" in capsys.readouterr().out
    (out / "summary.csv").unlink()
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert not (out / "summary.csv").exists()


def test_output_symlink_is_rejected(tmp_path):
    root = tmp_path / "results"
    case, path = write_case(root)
    external = tmp_path / "external.out"
    path.rename(external)
    path.symlink_to(external)
    record = extract_results(root)["records"][0]
    assert record["extraction_status"] == "unavailable"
    assert "outside" in " ".join(record["diagnostics"])
