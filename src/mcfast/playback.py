"""Read the main OpenFAST time series for completed-result playback."""
from pathlib import Path

import numpy as np
from openfast_io.FAST_output_reader import FASTOutputFile

CHANNEL_UNITS = {
    "Azimuth": "deg", "RotSpeed": "rpm",
    "PtfmSurge": "m", "PtfmSway": "m", "PtfmHeave": "m",
    "PtfmRoll": "deg", "PtfmPitch": "deg", "PtfmYaw": "deg",
    "Wind1VelX": "m/s", "GenPwr": "kW", "RotTorq": "kN-m",
}


def read_playback(run_dir: Path, model: str) -> dict:
    stem = Path(model).stem
    candidates = [run_dir / f"{stem}{suffix}" for suffix in (".outb", ".out")]
    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise ValueError("Main OpenFAST time-series output is missing.")
    # Never follow an artifact symlink outside its run directory.
    if source.resolve().parent != run_dir.resolve():
        raise ValueError("Result artifact is outside the run directory.")
    try:
        output = FASTOutputFile(str(source))
        data = output.data
        names = output.info["attribute_names"]
        units = output.info["attribute_units"]
        if data.ndim != 2 or data.shape[1] != len(names) or len(units) != len(names):
            raise ValueError("Inconsistent output columns.")
        time_index = names.index("Time")
        times = data[:, time_index]
        if units[time_index] not in {"s", "sec"}:
            raise ValueError("Unsupported time units.")
        if len(times) < 2 or not np.all(np.isfinite(times)) or not np.all(np.diff(times) > 0):
            raise ValueError("Playback needs at least two finite, increasing timestamps.")
        channels = {}
        missing = []
        for name, expected_unit in CHANNEL_UNITS.items():
            if name not in names:
                missing.append(name)
                continue
            index = names.index(name)
            values = data[:, index]
            if units[index] != expected_unit or not np.all(np.isfinite(values)):
                missing.append(name)
                continue
            channels[name] = {"unit": expected_unit, "values": values.tolist()}
        if "Azimuth" not in channels:
            raise ValueError("A finite Azimuth channel in degrees is required.")
        return {"available": True, "source": source.name, "timestamps": times.tolist(),
                "channels": channels, "missing_channels": missing}
    except Exception as exc:
        raise ValueError(f"Cannot read playback results: {exc}") from exc
