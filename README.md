# mcFAST

mcFAST (monte-carlo with openfast) is a local, browser-based workbench for OpenFAST input decks. It walks
the references from a primary `.fst` file, presents scalar inputs as editable
records, preserves the original text format on save, and renders a parametric
Three.js turbine/platform view from values in the model.

It supports creating variables and producing samples for batch simulations in a graphical user interface.


## Latest implementation

2026-10-06: 
- [X] Adding side bar menus about creating variables and basic sampling
- [X] Having checked hosting on remote server through ssh
- [X] Having checked the command line job submission using slurm.

---
## Quick start (macOS, Linux or WSL for windows)

Make sure the `openfast` is installed with `conda` 
(we recommend to have a `miniconda` as the package manager to install `openfast`).
This `miniconda` works across MacOS, Linux and WSL. When the `miniconda` is installed, 
please install and set the openfast in the path where you settle the `mcFAST` repository.
You can use `cd <my path of mcFAST>` to navigate to that dictionary, and run the following commands:

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

After the `openfast` and `mcFAST` are settled,
install [uv](https://docs.astral.sh/uv/) and [Node.js 20.19](https://nodejs.org/en/download) or newer, then run:

```bash
uv sync --extra dev
uv run python scripts/fetch_iea15mw.py
uv run python scripts/fetch_iea22mw.py
cd web && npm install && npm run build && cd ..

```

When everything is installed successfully, please try to launch the web-UI with
```bash
uv run mcfast
```

The user interface in the browser may look like this:
![Screenshot of webUI](doc/figs/screenshot_mcfast_UI.png)

The program may open http://127.0.0.1:8000 by default. The model fetchers extract only the OpenFAST
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

---
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

The persistent icon bar switches between **Files**, **Geometry**,
**Variable Study**, **UQ Method**, and **Simulation**. Click the active icon to collapse the panel, or another
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

Variable definitions can be saved with an empty case table, then loaded in
**UQ Method**. Set minimum and maximum bounds for each numeric variable and
choose uniform or truncated normal sampling (mean and positive standard
deviation). Integer variables use discrete uniform sampling with inclusive
integer bounds; Boolean and text variables retain their model values.
Distributions are independent. Choose **Monte Carlo**, **Latin hypercube (LHS)**,
**Sobol (scrambled)**, or **Halton (scrambled)**, then set the case count and seed.
Sobol requires a power-of-two count (1–65,536) to preserve sequence balance;
the other methods support any count within the study limits. LHS stratifies
each numeric variable into equal-probability intervals. Sobol and Halton use
scrambled low-discrepancy sequences. All methods map samples through the same
bounded distributions, with fixed variables excluded from sampling dimensions.
The selected method is saved with the study and restored when reopened.
Choose **Generate Preview**. Previewing leaves the saved study unchanged.
**Save Samples to Study** replaces its case table and saves the UQ configuration
in the study JSON. Open **Simulation** to run those cases. Sampling supports up
to 100,000 cases and 1,000,000 total values; a study with no cases cannot run.

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

## Parallel study simulations

Save the variables and case table in **Variable Study**, then open **Simulation**
from the left feature bar. Select the saved study and choose local worker slots. The local recommendation leaves one usable CPU for the application.
Capacity describes CPUs available to the process, not a measurement of idle CPUs;
large models also need sufficient memory and disk space.

**Run Study** freezes the project and sample table. Each active sample gets a
separate project copy, applies its scalar values, and runs the managed
TurbSim/OpenFAST pipeline. Changed managed TurbSim inputs regenerate their wind
fields. Original workspace inputs remain editable after the snapshot is created.
One slot covers preparation and execution. Native processes
use one computational thread, including OpenMP and common numerical libraries.
Ordinary UI runs share the local capacity limiter.

The panel shows sample status, execution phase, and result links. **OPEN** uses the existing console, playback, and graphs. Failed samples
do not stop the other cases. **Stop New Cases** cancels queued cases while active simulations finish. **Retry Failed Cases** creates new
attempts using the original snapshot and sample numbers; previous results remain.
One batch may be active per workspace.

After refreshing the project environment with `uv sync --extra dev`, start the
server with `uv run mcfast`. In another terminal:

```bash
uv run mcfast-study-run --workspace WORKSPACE_ID --study STUDY_ID --local-workers 4
uv run mcfast-study-run --workspace WORKSPACE_ID --study STUDY_ID --dry-run
```

The CLI connects to the server at `http://127.0.0.1:8000`; use `--server` to change
that address. With no allocations it selects the local recommendation. Dry-run
checks the saved variable bindings, sample types, local readiness, and slot
limits without launching a batch. Case-specific native input validation occurs
when each case starts. Ctrl+C stops new dispatches; active cases finish on the
server.

### Submit a study to Slurm in one command

On the cluster login node, submit a saved study with:

```bash
uv run mcfast-slurm submit workspaces/WORKSPACE_ID/studies/STUDY_ID.json
```

The command validates and freezes the study, writes its submission script,
selects the current Python interpreter, submits it with `sbatch`, and prints the
job ID and snapshot path. No separate preparation command or Python-path export
is needed. Logs are written inside the snapshot directory.

Resource defaults come from [scripts/mcfast-study.slurm](scripts/mcfast-study.slurm),
including your edits to that file. The template uses 10 nodes and 8 single-CPU
workers per node (up to 80 concurrent cases). Override settings when needed:

```bash
uv run mcfast-slurm submit workspaces/WORKSPACE_ID/studies/STUDY_ID.json \
  --nodes 10 --workers-per-node 8 --account ACCOUNT --partition PARTITION \
  --time 02:00:00 --mem-per-cpu 4G
```

For a job array, add `--array`. Each array task requests one node and runs the
chosen number of concurrent workers. For example:

```bash
uv run mcfast-slurm submit workspaces/WORKSPACE_ID/studies/STUDY_ID.json \
  --array --nodes 10 --workers-per-node 8
```

This submits 10 array tasks, each with 8 workers, with at most 10 array tasks
active at once. Cases are divided across all 80 workers without duplication.
Slurm may place multiple array tasks on the same physical node. Use the default
multi-node mode when you want one allocation spanning 10 distinct nodes.

The CLI also accepts `--workspace WORKSPACE_ID --study STUDY_ID` in place of a
study file, and `--workspace-root` for a custom workspace directory. Add
`--dry-run` to prepare the files and show the submission command without
submitting. Both modes submit once and return immediately; no web server is
required. Rejected submissions report Slurm's error and retain their snapshot.

Run from a Linux mcFAST environment on a filesystem shared by login and compute
nodes. Install the environment once with `uv sync --extra dev` and load the
site's OpenFAST/TurbSim modules before submitting. The model's native controller
libraries must be compatible with the cluster. All nodes need access to the
same Python environment, executables, snapshot, and result paths. Optional
`MCFAST_OPENFAST` and `MCFAST_TURBSIM` environment variables select explicit
solver paths.

Case preparation preserves controller paths such as
`../../../.openfast/conda-4.2.1/lib/libdiscon.so` by creating a `.openfast`
directory link beside each case's `project` directory, pointing to this
checkout's shared `.openfast` runtime. The relative path in ServoDyn stays
unchanged, and the Conda environment is not copied per case. Install the runtime
in the checkout on the cluster; it must remain accessible to all compute nodes.
This also applies to local study cases. Existing absolute controller paths
continue to use their configured location.

`--workers-per-node` is the number of simultaneous cases per node. Choose
memory per case and a wall time that covers all cases assigned to each worker,
including input copying and wind generation. For example, 160 cases on 80
workers require roughly two case runtimes plus preparation overhead, depending
on case durations. Wall time is not inferred from the selected nodes.

Each case gets a separate project copy and result directory. Native solvers
use one computational thread per case. Failed cases are recorded while the
other workers continue; the job exits with failure if any worker reports failed
cases. Results use the existing `workspaces/WORKSPACE_ID/results/` layout and
appear in the app's run history for that workspace.

```bash
squeue -j JOB_ID -o "%.18i %.10T %.6D %N"
sacct -j JOB_ID --format=JobID,State,NNodes,NodeList,ExitCode
uv run mcfast-slurm status --snapshot SNAPSHOT_PATH
uv run mcfast-slurm status --snapshot SNAPSHOT_PATH --details
```

Submission metadata is saved in `SNAPSHOT_PATH/submission.json`. Case progress
is read through `status`; these jobs are independent of the local Simulation
batch coordinator. Cancel with `scancel JOB_ID`. After cancellation or wall-time
expiry, unfinished case records can still say `running`; use Slurm accounting
for the job's final state. Submitting again creates a new snapshot and a new job.

The lower-level `prepare`, `worker`, and standalone `.slurm` script remain
available for custom workflows.

### SSH setup

The Simulation sidebar retains two optional setup fields: SSH login/host and
remote folder. These are saved in this browser for future configuration;
connecting, browsing remote folders, and remote execution through the sidebar
are not enabled. The Simulation panel uses local workers; submit Slurm studies
with the standalone script described above.

Batch records and frozen inputs live under `workspaces/WORKSPACE_ID/batches/`;
case outputs use the standard workspace `results/` directory. Unfinished cases
are marked interrupted after server restart and require explicit retry. Run one
mcFAST server process against a workspace directory and avoid reloads during
active simulations.

Additional APIs:

- `GET /api/simulation/targets` reports local capacity and readiness.
- `POST /api/workspaces/{id}/batches` accepts `study_id`, `slots: {"local": N}`, and optional `dry_run`.
- `GET /api/workspaces/{id}/batches` lists batch history.
- `GET /api/workspaces/{id}/batches/{batch}` accepts `page` and `page_size`.
- `POST /api/workspaces/{id}/batches/{batch}/stop` stops new dispatches.
- `POST /api/workspaces/{id}/batches/{batch}/retry` accepts new local `slots`.



## Extract case inputs and output statistics

Extract recorded study variables and tower-base bending statistics without running
OpenFAST or starting the web server:

```bash
uv run mcfast-extract \
  workspaces/iea-15-umainesemi-turbsim-v2-20261003-141931-72da34/results \
  --output workspaces/iea-15-umainesemi-turbsim-v2-20261003-141931-72da34/extracted
```

The default interval includes samples at **400 seconds and later**, excluding
startup. Use `--start 0` for the full record, or `--start 400 --end 3600` for a
bounded interval; both boundaries are inclusive. Add other output channels with
repeatable options such as `--channel GenPwr --channel PtfmPitch`. Existing
reports are protected; use `--overwrite` to replace them.

`summary.csv` has one row per discovered case, including incomplete cases in
nested folders. Columns include:

- `case_id`: case path relative to the supplied results folder.
- `run_id`, `study_id`, `sample_index`, and `simulation_status`: recorded run metadata.
- `input.NAME`: each variable in the run manifest's `sample_values`; missing
  values remain blank. Values are never inferred from a nearby study or current deck.
- `metric.CHANNEL.min`, `.max`, `.abs_max`, and their corresponding `_time`
  fields: signed extrema and maximum absolute magnitude, with occurrence times
  in seconds. Ties use the earliest sample. `.mean` and `.std` are the sample
  mean and population standard deviation (`ddof=0`). `.unit` preserves the
  output channel unit; `.valid_count` and `.excluded_count` describe coverage.
- `metric.tower_base_bending_resultant.max` and `.max_time`: the maximum of
  `sqrt(TwrBsMxt(t)^2 + TwrBsMyt(t)^2)` calculated at simultaneous timestamps.
  This requires matching component units and both values to be finite.
- `interval.*`: recorded bounds, actual analyzed bounds, and sample counts.
- `extraction_status`: `successful`, `partial` (some statistics available with
  diagnostics), or `unavailable` (no valid requested statistics).
- `manifest_source`, `input_source`, `output_source`, and `diagnostics`:
  provenance and reasons for missing or partial data.

`summary.json` contains the same data as structured records, plus extraction
settings, schema version, units, and status counts. Nonfinite channel values are
excluded and flagged. Simulation failure does not prevent extraction from a
readable output; simulation status is reported separately. Module outputs such
as `.MD.out` are excluded. Binary main outputs take precedence over text, and a
corrupt binary output is reported rather than silently replaced with text.

For Python callers, discovery, calculation, and export are separate:

```python
from mcfast.extract import extract_results, export_reports

report = extract_results("path/to/results", start=400, channels=["GenPwr"])
export_reports(report, "path/to/extracted")
```

# License

mcFAST is source-available under the [PolyForm Noncommercial License 1.0.0](LICENSE).
The license permits noncommercial use, modification, and redistribution, subject
to its terms, including the requirement to pass along the license and any required
notices. It also expressly permits use by the noncommercial organizations listed
in the license, including educational institutions and public research organizations.

Commercial use, including commercial sale of mcFAST or modified versions, requires
a separate written agreement with the copyright holder. Contact the project
maintainer to discuss commercial licensing. This restriction covers commercial
use of the software, not only its sale; the full license defines permitted purposes.

Third-party dependencies and downloaded turbine models retain their respective
licenses. OpenFAST is separately licensed under the
[Apache License 2.0](https://github.com/OpenFAST/openfast/blob/main/LICENSE).
The mcFAST license does not replace or modify those third-party licenses.



