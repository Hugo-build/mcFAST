# mcFAST

mcFAST is a local, browser-based workbench for OpenFAST input decks. It walks
the references from a primary `.fst` file, presents scalar inputs as editable
records, preserves the original text format on save, and renders a parametric
Three.js turbine/platform view from values in the model.

## Quick start (macOS and Linux)

Install [uv](https://docs.astral.sh/uv/) and Node.js 20.19 or newer, then run:

```bash
uv sync --extra dev
uv run python scripts/fetch_iea15mw.py
uv run python scripts/fetch_iea22mw.py
cd web && npm install && npm run build && cd ..
uv run mcfast
```

![Screenshot of webUI](doc/figs/screenshot_mcfast_UI.png)

Open <http://127.0.0.1:8000>. The model fetchers extract only the OpenFAST
subtrees from pinned official releases. The IEA Wind 15 MW fetcher uses v1.1.17
and includes the VolturnUS-S/UMaineSemi input deck. The IEA Wind 22 MW fetcher
uses v1.1.0 and includes both monopile and semisubmersible input decks from the
[official model repository](https://github.com/IEAWindSystems/IEA-22-280-RWT).
Both commands are safe to rerun; pass `--force` to replace an existing download.

For frontend development, run the API and Vite separately:

```bash
uv run mcfast --reload
cd web && npm run dev
```

Vite proxies `/api` to the Python process during development. For a production
build, the Python app serves `web/dist` directly.

## OpenFAST versus `openfast_io`

The uv environment installs the official `openfast_io` Python package from
PyPI. It reads and writes OpenFAST data, but it is **not** the native simulation
executable. `uv` cannot install Conda packages. The downloaded IEA 15 MW v1.1.17
deck targets OpenFAST 4.1, so use the compatible OpenFAST 4.2.1 Conda package
instead of the current Homebrew 5.x release:

```bash
# Creates an isolated native executable environment inside the project.
conda create --yes --prefix .openfast/conda-4.2.1 \
  --channel conda-forge --override-channels openfast=4.2.1

# Install the model's matching ROSCO controller without its optional Python UI stack.
conda install --yes --no-deps --prefix .openfast/conda-4.2.1 \
  --channel conda-forge --override-channels rosco=2.10.1
conda install --yes --prefix .openfast/conda-4.2.1 \
  --channel conda-forge --override-channels zeromq=4.3.5

# Expose that executable to the uv virtual environment.
cp scripts/openfast .venv/bin/openfast
chmod +x .venv/bin/openfast
```

Then run a deck through the uv-managed command:

```bash
uv run mcfast-run models/IEA-15-240-RWT/IEA-15-240-RWT-UMaineSemi/IEA-15-240-RWT-UMaineSemi.fst
```

The launcher resolves `.openfast/conda-4.2.1/bin/openfast` relative to this
checkout. The model fetch also adds the OpenFAST 4.2 `HubIner_Teeter` field and
points ServoDyn at the native ROSCO library. Use `--dry-run` to validate the
deck and command without starting a simulation. During a real run, console
output remains live and is also saved with the generated outputs and a JSON
manifest under `results/openfast/<run-id>/`.

The same native workflow is available in the browser: select an input deck,
press **RUN OPENFAST**, and expand **RESULTS / CONSOLE**. The console is streamed
while the process runs, and every saved result is linked when it becomes
available.

## Result playback

Completed runs load paused in the 3D viewport. Use **Play/Pause**, **Replay**,
the simulation-time slider, and the 0.25×–2× speed selector to inspect the
recorded rotor azimuth and rigid platform motion. The clock and rotor speed,
wind speed, generator power, and rotor torque readouts follow the same result
frame. Motion uses physical scale; there is no artificial platform bobbing.

Playback reads the main `.outb` (preferred) or `.out` file and requires finite
azimuth values plus at least two increasing timestamps. Missing optional
channels are identified beside the controls; their telemetry is unavailable,
and missing platform motion contributes zero displacement or rotation. New
runs save their geometry and platform reference point. Older runs display a
notice that playback uses current workspace geometry.

A reproducible first check is the IEA 15 MW UMaineSemi deck with `WindType = 1`,
`HWindSpeed = 10`, and `TMax = 10` in an isolated workspace. Constant wind is
only the test setup; other completed runs with the required channels also play.

## Time-series results

The graph icon on the right opens the Results panel. Select a saved completed
run, search output channels by name or unit, and inspect stacked graphs.
Start/end inputs and drag-to-zoom control an interval independent of playback;
Reset range restores the full run. CSV exports original samples in the inclusive
interval, while PNG exports the displayed graphs with run and source details.

Playback and graphs share one backend output store. Binary formats 1–4 use
read-only memory mapping and decode requested columns only; metadata does not
decompress channel data. Text output is converted in bounded chunks into a
temporary disk-backed array. Decoded columns use a 64 MiB backend cache, and
browser channels use a conservative 32 MiB cache allowance. Playback channel
arrays are also reused for plots. Switching runs or workspaces clears browser
plot data, aborts pending browser requests, and releases the old backend cache;
in-flight backend requests retain valid references until they finish. Cache
limits exclude active responses and operating-system file pages. Closing the
panel preserves current selections.

## Left feature panels

The persistent icon bar switches between **Files**, **Geometry**, and
**Variable Study**. Click the active icon to collapse the panel, or another
icon to switch features. The workspace selector is shared across all panels.
The study editor uses a wider panel on desktop and an overlay on small screens;
unsaved values survive panel switches but reset when changing workspaces.

Geometry lists generated turbine parts and the supported platform GDF source.
Tower geometry uses the linked AeroDyn file's `NumTwrNds`, `TwrElev`, and
`TwrDiam` table: radii are half the diameters, with a linear taper between
stations at their specified elevations. The Geometry panel shows the source
file. Models without a valid table and older saved runs without a tower profile
use the schematic tower dimensions (7 m base radius and 2.4 m top radius).
Visibility checkboxes affect only the 3D display and persist through playback
and scene rebuilds. Ocean and weather rendering are reserved for future work.

## Variable-study workspaces

Open **Variable Study** from the vertical feature bar on the left
to prepare explicit study cases without modifying the source model. Each
variable is bound to a linked input file and an exact scalar parameter name.
Selected variables become columns in an editable case table, so numeric,
integer, Boolean, and text values can be entered directly. CSV files with
matching variable headers append their valid rows to the existing table.
Discovered TurbSim `.in` files appear alongside the linked OpenFAST inputs and
their scalar parameters can be used as study variables in the same way.

Creating the workspace copies the model's common source tree (including
ancillary blade, airfoil, wind, controller, and hydrodynamic files) under
`workspaces/<workspace-id>/project/`. Variable bindings and case rows are saved
under `workspaces/<workspace-id>/studies/`; the study panel also provides a direct
JSON download.

## TurbSim wind fields

The OpenFAST 4.2.1 Conda environment also contains the native `turbsim`
executable. This repository includes a TurbSim input at
`models/IEA-15-240-RWT/IEA-15-240-RWT/Wind/IEA15MW_IEC_ETM_U50.0_Seed60362647.in`.
Generate a reproducible group of UMaineSemi normal-turbulence inputs with:

```bash
uv run python scripts/generate_turbsim_inputs.py \
  --wind-speeds 8 10 12 \
  --seeds 101 202 303 \
  --analysis-time 60
```

This creates nine `.in` files in the shared `Wind` directory without changing
the supplied template. Add `--run` to execute TurbSim immediately and generate
the nine corresponding `.bts` files. Existing inputs are protected unless
`--overwrite` is explicitly passed. The defaults generate three 10 m/s NTM
inputs with seeds 101, 202, and 303; `--help` lists the IEC class, turbulence
category, timing, naming, and output-directory controls.

`WrADFF = True` in each generated input produces a same-stem `.bts` full-field wind file.
To use it in this model, set `WindType = 3` and `FileName_BTS` to the `.bts`
path in `IEA-15-240-RWT_InflowFile.dat`. The downloaded deck currently uses
`WindType = 1`, so its default 10-second test run is steady wind.

For workspace projects, setting `WindType = 3` also exposes a TurbSim section
under the parsed InflowWind file. Selecting an imported `.in` makes its
same-stem `.bts` the managed wind field. **Run OpenFAST** generates that output
only when it is missing or older than the `.in`; otherwise the existing wind
field is reused. If `FileName_BTS` is edited to another workspace `.bts`, the
workspace switches to external mode and runs OpenFAST without invoking TurbSim.

## API

- `GET /api/sources` discovers primary `.fst` inputs under `models/`.
- `POST /api/workspaces` imports an isolated workspace from a local `.fst` deck.
- `GET /api/workspaces/{workspace-id}/model` resolves linked files and derived geometry.
- `GET`/`PUT /api/workspaces/{workspace-id}/file?path=…` reads or losslessly updates scalar parameters.
- `GET /api/workspaces/{workspace-id}/wind` reports TurbSim candidates, mode, and output freshness.
- `PUT /api/workspaces/{workspace-id}/wind` selects a managed `.in` and updates `FileName_BTS`.
- `POST /api/workspaces/{workspace-id}/runs` starts the TurbSim/OpenFAST pipeline in the background.
- `GET /api/workspaces/{workspace-id}/runs/{run-id}?offset=…` returns incremental console output and state.
- `GET /api/workspaces/{workspace-id}/runs/{run-id}/results` returns source, time bounds, sample count, and channel names/units.
- `GET /api/workspaces/{workspace-id}/runs/{run-id}/results/series?channel=RotSpeed` returns full-resolution selected channels, with optional inclusive `start`/`end`.
- `DELETE /api/workspaces/{workspace-id}/runs/{run-id}/results/cache` releases that run’s cached reader.
- `GET /api/workspaces/{workspace-id}/runs/{run-id}/playback` returns availability, timestamps, channel values/units, missing channels, and playback geometry.

The conservative parser does not rewrite tables or output-channel lists. That
keeps round trips safe while table-aware editing can be added format by format.

## Verification

```bash
uv run pytest
cd web && npm test && npm run build
```

The integration test uses the downloaded official IEA 15 MW VolturnUS-S deck;
without it, only that test is skipped.
