"""General main-output time series, independent of animation channels."""
from pathlib import Path
from collections import Counter

import numpy as np
from .output_store import Channel, get_store


def read_results(run_dir: Path, model: str):
    source = next((run_dir / f"{Path(model).stem}{suffix}" for suffix in (".outb", ".out")
                   if (run_dir / f"{Path(model).stem}{suffix}").is_file()), None)
    if source is None:
        raise ValueError("Main OpenFAST time-series output is missing.")
    if source.resolve().parent != run_dir.resolve():
        raise ValueError("Result artifact is outside the run directory.")
    try:
        store = get_store(source)
        names, units, times = store.names, store.units, store.times
        index = 0
        counts = Counter(names)
        channels = {}
        for i, name in enumerate(names):
            if i == index:
                continue
            label = f"{name} [column {i + 1}]" if counts[name] > 1 else name
            while label in channels:
                label += " [duplicate]"
            channels[label] = Channel(store, i - 1, units[i])
        return source.name, times, channels
    except Exception as exc:
        raise ValueError(f"Cannot read results: {exc}") from exc


def result_metadata(result):
    source, times, channels = result
    return {"available": True, "source": source, "start": float(times[0]), "end": float(times[-1]),
            "sample_count": len(times), "channels": [{"name": name, "unit": channel["unit"]} for name, channel in channels.items()]}


def result_series(result, selected, start=None, end=None):
    source, times, channels = result
    start = float(times[0]) if start is None else start
    end = float(times[-1]) if end is None else end
    if not np.isfinite(start) or not np.isfinite(end) or start > end or start < times[0] or end > times[-1]:
        raise ValueError("Choose a finite time interval within the run bounds, with start ≤ end.")
    if not selected or any(name not in channels for name in selected):
        raise ValueError("Select valid output channels.")
    mask = (times >= start) & (times <= end)
    return {"source": source, "timestamps": times[mask].tolist(), "channels": {
        name: {"unit": channels[name]["unit"], "values": [float(v) if np.isfinite(v) else None for v in channels[name]["values"][mask]]}
        for name in dict.fromkeys(selected)}}
