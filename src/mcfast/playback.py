"""Read the main OpenFAST time series for completed-result playback."""
from pathlib import Path

import numpy as np
from .results import read_results

CHANNEL_UNITS = {
    "Azimuth": "deg", "RotSpeed": "rpm",
    "PtfmSurge": "m", "PtfmSway": "m", "PtfmHeave": "m",
    "PtfmRoll": "deg", "PtfmPitch": "deg", "PtfmYaw": "deg",
    "Wind1VelX": "m/s", "GenPwr": "kW", "RotTorq": "kN-m",
}


def read_playback(run_dir: Path, model: str) -> dict:
    try:
        source, times, available = read_results(run_dir, model)
        channels = {}
        missing = []
        for name, expected_unit in CHANNEL_UNITS.items():
            if name not in available:
                missing.append(name)
                continue
            values = available[name]["values"]
            if available[name]["unit"] != expected_unit or not np.all(np.isfinite(values)):
                missing.append(name)
                continue
            channels[name] = {"unit": expected_unit, "values": values.tolist()}
        if "Azimuth" not in channels:
            raise ValueError("A finite Azimuth channel in degrees is required.")
        return {"available": True, "source": source, "timestamps": times.tolist(),
                "channels": channels, "missing_channels": missing}
    except Exception as exc:
        raise ValueError(f"Cannot read playback results: {exc}") from exc
