"""Join recorded case inputs with bounded OpenFAST output summaries."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re

import numpy as np

from .output_store import get_store, release_store

DEFAULT_CHANNELS = ("TwrBsMxt", "TwrBsMyt")
MODULE_SUFFIX = re.compile(r"\.(?:AD\d*|BD\d*|ED|HD|MD|SD|SrvD|IfW|SeaSt|SeaState|RO)\.(?:out|outb)$", re.I)


def discover_cases(root: Path) -> list[Path]:
    """Find artifact-bearing directories, including incomplete and empty cases."""
    cases = set()
    for path in root.rglob("*"):
        if path.is_dir() and re.search(r"(?:^|-)case-\d+$", path.name):
            cases.add(path)
        elif path.is_file() and (path.name in {"manifest.json", "console.log"}
                                 or path.suffix.lower() in {".out", ".outb", ".sum", ".dbg"}):
            cases.add(path.parent)
    if (root / "manifest.json").is_file():
        cases.add(root)
    return sorted(cases, key=lambda path: path.relative_to(root).as_posix())


def load_inputs(case: Path) -> tuple[dict, dict, list[str]]:
    path = case / "manifest.json"
    if not path.is_file():
        return {}, {}, ["Missing manifest.json; recorded inputs and simulation status are unavailable."]
    try:
        manifest = json.loads(path.read_text())
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be a JSON object")
    except (OSError, ValueError) as exc:
        return {}, {}, [f"Invalid manifest.json: {exc}"]
    inputs = manifest.get("sample_values")
    diagnostics = []
    if not isinstance(inputs, dict):
        inputs = {}
        diagnostics.append("Missing or invalid sample_values; recorded inputs are unavailable.")
    clean = {}
    for name, value in inputs.items():
        if isinstance(value, float) and not np.isfinite(value):
            clean[name] = None
            diagnostics.append(f"Nonfinite input: {name}")
        elif value is None or isinstance(value, (str, bool, int, float)):
            clean[name] = value
        else:
            clean[name] = None
            diagnostics.append(f"Unsupported non-scalar input: {name}")
    return manifest, clean, diagnostics


def select_output(case: Path, manifest: dict) -> Path:
    model = manifest.get("model") or manifest.get("workspace_entry")
    if isinstance(model, str) and model:
        stem = Path(model).stem
        candidates = [case / (stem + suffix) for suffix in (".outb", ".out")]
    else:
        outputs = [p for p in case.iterdir() if p.is_file()
                   and p.suffix.lower() in {".outb", ".out"}
                   and not MODULE_SUFFIX.search(p.name)]
        stems = {p.stem for p in outputs}
        if len(stems) > 1:
            raise ValueError("Ambiguous main output; provide a manifest identifying the model.")
        candidates = sorted(outputs, key=lambda p: (p.suffix.lower() != ".outb", p.name))
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        raise ValueError("Main OpenFAST time-series output is missing.")
    if source.resolve().parent != case.resolve():
        raise ValueError("Result artifact is outside the case directory.")
    return source


def channel_statistics(times: np.ndarray, values: np.ndarray, unit: str) -> dict:
    finite = np.isfinite(values)
    result = {"unit": unit, "valid_count": int(finite.sum()),
              "excluded_count": int((~finite).sum())}
    if not finite.any():
        return result
    values, times = values[finite], times[finite]
    low, high, absolute = int(np.argmin(values)), int(np.argmax(values)), int(np.argmax(np.abs(values)))
    scale = float(np.max(np.abs(values)))
    normalized = values / scale if scale else values
    result.update(min=float(values[low]), min_time=float(times[low]),
                  max=float(values[high]), max_time=float(times[high]),
                  abs_max=float(abs(values[absolute])), abs_max_time=float(times[absolute]),
                  mean=float(np.mean(normalized) * scale), std=float(np.std(normalized, ddof=0) * scale))
    return result


def calculate_metrics(source: Path, channels: list[str], start: float, end: float | None) -> tuple[dict, dict, list[str]]:
    store = get_store(source)
    stop = float(store.times[-1]) if end is None else end
    left = int(np.searchsorted(store.times, start, side="left"))
    right = int(np.searchsorted(store.times, stop, side="right"))
    times = store.times[int(left):right]
    interval = {"recorded_start": float(store.times[0]), "recorded_end": float(store.times[-1]),
                "recorded_sample_count": len(store.times), "sample_count": len(times),
                "analysis_start": float(times[0]) if len(times) else None,
                "analysis_end": float(times[-1]) if len(times) else None}
    if not len(times):
        return {}, interval, ["No samples in the requested analysis interval."]
    metrics, values, diagnostics = {}, {}, []
    counts = Counter(store.names)
    for name in channels:
        if counts[name] != 1:
            diagnostics.append(f"{'Missing' if counts[name] == 0 else 'Ambiguous duplicate'} channel: {name}")
            continue
        index = store.names.index(name)
        if index == 0:
            data = times
        else:
            data = store.values(index - 1)[int(left):right]
        metric = channel_statistics(times, data, store.units[index])
        metrics[name] = metric
        if name in DEFAULT_CHANNELS:
            values[name] = data
        if metric["excluded_count"]:
            diagnostics.append(f"{name}: excluded {metric['excluded_count']} nonfinite samples.")
        if not metric["valid_count"]:
            diagnostics.append(f"{name}: no finite samples.")
    if all(name in values for name in DEFAULT_CHANNELS):
        mx, my = (values[name] for name in DEFAULT_CHANNELS)
        units = [metrics[name]["unit"] for name in DEFAULT_CHANNELS]
        if units[0] != units[1] or not units[0]:
            diagnostics.append("Tower-base resultant requires matching, nonempty component units.")
        else:
            finite = np.isfinite(mx) & np.isfinite(my)
            resultant = np.full(len(times), np.nan)
            resultant[finite] = np.hypot(mx[finite], my[finite])
            summary = channel_statistics(times, resultant, units[0])
            metrics["tower_base_bending_resultant"] = {key: summary[key] for key in
                ("unit", "valid_count", "excluded_count", "max", "max_time") if key in summary}
            if summary["excluded_count"]:
                diagnostics.append(f"Tower-base resultant: excluded {summary['excluded_count']} nonfinite pairs or results.")
            if not summary["valid_count"]:
                diagnostics.append("Tower-base resultant: no finite pairs.")
    return metrics, interval, diagnostics


def extract_results(results_dir: str | Path, *, start: float = 400.0,
                    end: float | None = None, channels: list[str] | None = None) -> dict:
    """Extract one record per case without writing reports or changing sources."""
    root = Path(results_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Results directory does not exist: {root}")
    if not np.isfinite(start) or (end is not None and (not np.isfinite(end) or end < start)):
        raise ValueError("Choose finite start/end times with start <= end.")
    selected = list(dict.fromkeys([*DEFAULT_CHANNELS, *(channels or [])]))
    records = []
    for case in discover_cases(root):
        manifest, inputs, diagnostics = load_inputs(case)
        record = {"case_id": case.relative_to(root).as_posix(), "case_path": str(case),
                  "run_id": manifest.get("run_id", case.name), "study_id": manifest.get("study_id"),
                  "sample_index": manifest.get("sample_index"),
                  "simulation_status": manifest.get("status", "unknown"),
                  "manifest_source": str(case / "manifest.json") if (case / "manifest.json").is_file() else None,
                  "input_source": "manifest.sample_values" if "sample_values" in manifest else None,
                  "output_source": None, "inputs": inputs, "metrics": {},
                  "interval": {}, "diagnostics": diagnostics}
        try:
            source = select_output(case, manifest)
            record["output_source"] = str(source)
            metrics, interval, issues = calculate_metrics(source, selected, start, end)
            record.update(metrics=metrics, interval=interval)
            diagnostics.extend(issues)
        except (OSError, ValueError, IndexError, UnicodeError) as exc:
            diagnostics.append(f"Cannot extract output: {exc}")
        finally:
            release_store(case)
        available = any(metric.get("valid_count", 0) for metric in record["metrics"].values())
        record["extraction_status"] = "unavailable" if not available else "partial" if diagnostics else "successful"
        records.append(record)
    counts = {status: sum(r["extraction_status"] == status for r in records)
              for status in ("successful", "partial", "unavailable")}
    return {"schema_version": 1, "results_dir": str(root),
            "settings": {"start": start, "end": end, "channels": selected,
                         "std_ddof": 0, "resultant": "sqrt(TwrBsMxt(t)^2 + TwrBsMyt(t)^2)"},
            "counts": counts, "records": records}


def export_reports(report: dict, output_dir: str | Path, *, overwrite: bool = False) -> tuple[Path, Path]:
    directory = Path(output_dir).expanduser().resolve()
    paths = (directory / "summary.csv", directory / "summary.json")
    if not overwrite and any(path.exists() for path in paths):
        raise FileExistsError("Report already exists; use --overwrite to replace it.")
    rows = []
    base_fields = ["case_id", "case_path", "run_id", "study_id", "sample_index", "simulation_status",
                   "extraction_status", "manifest_source", "input_source", "output_source"]
    for record in report["records"]:
        row = {key: record[key] for key in base_fields}
        row.update({f"input.{key}": value for key, value in record["inputs"].items()})
        row.update({f"interval.{key}": value for key, value in record["interval"].items()})
        for name, metric in record["metrics"].items():
            row.update({f"metric.{name}.{key}": value for key, value in metric.items()})
        row["diagnostics"] = json.dumps(record["diagnostics"])
        rows.append(row)
    fields = base_fields + sorted({key for row in rows for key in row} - set(base_fields) - {"diagnostics"}) + ["diagnostics"]
    # Serialize before opening destinations so invalid JSON cannot truncate a report.
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    directory.mkdir(parents=True, exist_ok=True)
    mode = "w" if overwrite else "x"
    with paths[0].open(mode, newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with paths[1].open(mode) as stream:
        stream.write(payload)
    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Directory for summary.csv and summary.json")
    parser.add_argument("--start", type=float, default=400.0)
    parser.add_argument("--end", type=float)
    parser.add_argument("--channel", action="append", default=[], help="Additional channel (repeatable)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        if not args.overwrite and any((args.output / name).exists() for name in ("summary.csv", "summary.json")):
            raise FileExistsError("Report already exists; use --overwrite to replace it.")
        report = extract_results(args.results_dir, start=args.start, end=args.end, channels=args.channel)
        paths = export_reports(report, args.output, overwrite=args.overwrite)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Cases: {len(report['records'])}; " + "; ".join(f"{key}: {value}" for key, value in report["counts"].items()))
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
