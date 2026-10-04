import { createSimulationPanel } from './simulation-panel.js';
import './style.css';
import { createScene } from './scene.js';
import { createPlayback } from './playback.js';
import { createResultsPanel } from './results-panel.js';

const workspaceSelect = document.querySelector('#workspaceSelect');
const fileTree = document.querySelector('#fileTree');
const fileCount = document.querySelector('#fileCount');
const activeModelName = document.querySelector('#activeModelName');
const viewportModelLabel = document.querySelector('#viewportModelLabel');
const statusDot = document.querySelector('#statusDot');
const statusText = document.querySelector('#statusText');
const simClock = document.querySelector('#simClock');
const toast = document.querySelector('#toast');
const sidePanel = document.querySelector('#sidePanel');
const resultsDrawer = document.querySelector('#resultsDrawer');
const consoleOutput = document.querySelector('#consoleOutput');
const runBadge = document.querySelector('#runBadge');
const runArtifacts = document.querySelector('#runArtifacts');
const gaugeFill = document.querySelector('#gaugeFill');
const rpmValue = document.querySelector('#rpmValue');
const powerValue = document.querySelector('#powerValue');
const windValue = document.querySelector('#windValue');
const torqueValue = document.querySelector('#torqueValue');
const progressTrack = document.querySelector('#progressTrack');
const progressFill = document.querySelector('#progressFill');
const runBtn = document.querySelector('#runBtn');
const runBtnLabel = document.querySelector('#runBtnLabel');
const themeToggle = document.querySelector('#themeToggle');
const themeLabel = document.querySelector('#themeLabel');
const workspaceForm = document.querySelector('#workspaceForm');
const workspaceName = document.querySelector('#workspaceName');
const workspaceSource = document.querySelector('#workspaceSource');
const workspaceSourceMeta = document.querySelector('#workspaceSourceMeta');
const variableList = document.querySelector('#variableList');
const emptyVariableBtn = document.querySelector('#emptyVariableBtn');
const caseTable = document.querySelector('#caseTable');
const caseTableHead = document.querySelector('#caseTableHead');
const caseTableBody = document.querySelector('#caseTableBody');
const emptyCaseBtn = document.querySelector('#emptyCaseBtn');
const selectAllCases = document.querySelector('#selectAllCases');
const clearSelectedCasesBtn = document.querySelector('#clearSelectedCasesBtn');
const clearCasesBtn = document.querySelector('#clearCasesBtn');
const csvInput = document.querySelector('#csvInput');
const csvStatus = document.querySelector('#csvStatus');
const workspaceResult = document.querySelector('#workspaceResult');
const modalFootnote = document.querySelector('#modalFootnote');
const createWorkspaceBtn = document.querySelector('#createWorkspaceBtn');
const studySelect = document.querySelector('#studySelect');
const runHistorySelect = document.querySelector('#runHistorySelect');
const importModal = document.querySelector('#importModal');
const importForm = document.querySelector('#importForm');
const importName = document.querySelector('#importName');
const sourceSelect = document.querySelector('#sourceSelect');
const sourcePath = document.querySelector('#sourcePath');
const confirmImportBtn = document.querySelector('#confirmImportBtn');
const visual = createScene(document.querySelector('#scene'));
const resultsPanel = createResultsPanel({ request, selectRun: runId => {
  runHistorySelect.value = runId;
  return showHistoricalRun(runId).catch(error => notify(error.message));
} });

workspaceForm.inert = true;
const featureButtons = [...document.querySelectorAll('[data-feature]')];
let activeFeature = 'files';
const geometryVisibility = { tower: true, nacelle: true, hub: true, blades: true, platform: true, grid: true };

function selectFeature(feature, toggle = false) {
  const collapsed = toggle && activeFeature === feature && !sidePanel.classList.contains('collapsed');
  activeFeature = feature;
  sidePanel.classList.toggle('collapsed', collapsed);
  sidePanel.classList.toggle('study-active', feature === 'study' || feature === 'simulation');
  sidePanel.inert = collapsed;
  document.querySelector('#panelTitle').textContent = { files: 'WORKSPACE FILES', geometry: 'GEOMETRY', study: 'VARIABLE STUDY', simulation: 'SIMULATION' }[feature];
  for (const button of featureButtons) {
    const selected = button.dataset.feature === feature && !collapsed;
    button.classList.toggle('active', selected);
    button.setAttribute('aria-pressed', String(selected));
    button.setAttribute('aria-expanded', String(selected));
    document.querySelector(`#${button.dataset.feature}Panel`).hidden = button.dataset.feature !== feature;
  }
  if (feature === 'simulation' && !collapsed) simulationPanel.refresh().catch(error => notify(error.message));
  if (feature === 'study' && !collapsed && !workspaceForm.inert && activeModel && studyWorkspaceId !== activeWorkspace?.workspace_id) resetStudyForm();
}

function rebuildGeometry(geometry, context = 'Current workspace geometry') {
  visual.rebuild(geometry);
  document.querySelector('#geometryContext').textContent = context;
  const list = document.querySelector('#geometryList');
  list.replaceChildren();
  const entries = [
    ['tower', 'Tower', geometry?.towerProfile ? `${geometry.towerProfile.source} · TwrElev / TwrDiam` : 'Schematic tower · no supported AeroDyn diameter table'],
    ['nacelle', 'Nacelle', 'Generated geometry'],
    ['hub', 'Hub', 'Generated geometry'],
    ['blades', 'Blades', 'Generated from OpenFAST parameters'],
    ['platform', 'Floating platform', geometry?.floater ? `${geometry.floater.source} · ${geometry.floater.format}` : 'No supported platform geometry available'],
    ['grid', 'Reference grid', 'Scene reference'],
  ];
  for (const [part, name, source] of entries) {
    const label = document.createElement('label');
    label.className = 'geometry-item';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = geometryVisibility[part];
    checkbox.disabled = !geometry || (part === 'platform' && !geometry.floater);
    if (checkbox.disabled) checkbox.checked = false;
    checkbox.onchange = () => {
      geometryVisibility[part] = checkbox.checked;
      visual.setVisibility(part, checkbox.checked);
    };
    const copy = document.createElement('span');
    const title = document.createElement('strong');
    title.textContent = name;
    const detail = document.createElement('small');
    detail.textContent = source;
    copy.append(title, detail);
    label.append(checkbox, copy);
    list.append(label);
  }
}

const GAUGE_CIRC = 2 * Math.PI * 33;
const RATED_RPM = 12.1;
let activeModel = null;
let activeWorkspace = null;
let activeNode = null;
let activeWind = null;
let running = false;
let currentRunId = null;
let studyWorkspaceId = null;
let studyGeneration = 0;
let editingStudyId = null;
let savedStudies = [];
let sourceModels = [];
let parameterControlSequence = 0;
let variableSequence = 0;
let caseSequence = 0;
let caseRows = [];
let studyDirty = false;
const parameterCache = new Map();

function workspaceUrl(path = '') {
  if (!activeWorkspace) throw new Error('Select a workspace first');
  return `/api/workspaces/${encodeURIComponent(activeWorkspace.workspace_id)}${path}`;
}

function applyTheme(theme, persist = true) {
  const selected = theme === 'light' ? 'light' : 'dark';
  document.documentElement.dataset.theme = selected;
  themeLabel.textContent = selected.toUpperCase();
  themeToggle.setAttribute('aria-pressed', String(selected === 'light'));
  themeToggle.setAttribute('aria-label', `Switch to ${selected === 'light' ? 'dark' : 'light'} mode`);
  visual.setTheme(selected);
  document.querySelector('meta[name="theme-color"]').content = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
  if (persist) localStorage.setItem('mcfast-theme', selected);
}

function setStatus(mode, label) {
  statusDot.className = `status-dot ${mode}`;
  statusText.textContent = label;
}

function notify(message) {
  toast.textContent = message;
  toast.classList.add('show');
  window.setTimeout(() => toast.classList.remove('show'), 2200);
}

function safeText(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[character]);
}

async function request(url, options) {
  const response = await fetch(url, options);
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  return response.json();
}

function fmtTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remainder = (seconds % 60).toFixed(1).padStart(4, '0');
  return `${String(minutes).padStart(2, '0')}:${remainder}`;
}

function renderTelemetry(now) {
  const seconds = lastPlaybackFrame === null ? 0 : (now - lastPlaybackFrame) / 1000;
  lastPlaybackFrame = now;
  if (playback) {
    playback.advance(document.hidden ? 0 : seconds);
    renderPlaybackFrame();
  }
  requestAnimationFrame(renderTelemetry);
}

const playbackMessage = document.querySelector('#playbackMessage');
const playbackControls = document.querySelector('#playbackControls');
const playbackPlay = document.querySelector('#playbackPlay');
const playbackSeek = document.querySelector('#playbackSeek');
const playbackTime = document.querySelector('#playbackTime');
const playbackSpeed = document.querySelector('#playbackSpeed');
let playback = null;
let playbackData = null;
let playbackGeneration = 0;
let playbackRequest = null;
let lastPlaybackFrame = null;

function clearResultTelemetry() {
  rpmValue.textContent = '—';
  powerValue.textContent = '— kW';
  windValue.textContent = '— m/s';
  torqueValue.textContent = '— kNm';
  gaugeFill.setAttribute('stroke-dashoffset', String(GAUGE_CIRC));
}

function resetPlayback(message = 'Select a completed run for playback.') {
  playbackGeneration++;
  playbackRequest?.abort(); playbackRequest = null;
  playback = null;
  playbackData = null;
  lastPlaybackFrame = null;
  playbackControls.hidden = true;
  playbackMessage.textContent = message;
  rebuildGeometry(activeModel?.geometry);
  clearResultTelemetry();
  simClock.textContent = fmtTime(0);
}

function renderPlaybackFrame() {
  const frame = playback.sample();
  visual.applyFrame(frame);
  rpmValue.textContent = frame.RotSpeed?.toFixed(1) ?? '—';
  powerValue.textContent = frame.GenPwr === undefined ? '— kW' : `${Math.round(frame.GenPwr).toLocaleString()} kW`;
  windValue.textContent = frame.Wind1VelX === undefined ? '— m/s' : `${frame.Wind1VelX.toFixed(1)} m/s`;
  torqueValue.textContent = frame.RotTorq === undefined ? '— kNm' : `${frame.RotTorq.toFixed(1)} kNm`;
  gaugeFill.setAttribute('stroke-dashoffset', String(GAUGE_CIRC * (1 - Math.min(1, Math.max(0, (frame.RotSpeed ?? 0) / RATED_RPM)))));
  simClock.textContent = fmtTime(playback.time);
  playbackSeek.value = playback.time;
  playbackSeek.setAttribute('aria-valuetext', `${playback.time.toFixed(2)} seconds`);
  playbackTime.textContent = `${playback.time.toFixed(2)} / ${playbackData.timestamps.at(-1).toFixed(2)} s`;
  playbackPlay.textContent = playback.playing ? 'Pause' : 'Play';
}

async function loadPlayback(runId) {
  resetPlayback('Loading completed results…');
  const generation = playbackGeneration;
  const url = workspaceUrl(`/runs/${encodeURIComponent(runId)}/playback`);
  const controller = playbackRequest = new AbortController();
  try {
    const data = await request(url, { signal: controller.signal });
    if (generation !== playbackGeneration) return;
    if (!data.available) { playbackMessage.textContent = data.reason; return; }
    playbackData = data;
    playback = createPlayback(data);
    rebuildGeometry(data.geometry, data.geometry_source === 'saved' ? 'Saved run geometry' : 'Current workspace geometry · older run');
    playbackSeek.min = data.timestamps[0];
    playbackSeek.max = data.timestamps.at(-1);
    playbackSpeed.value = '1';
    playbackControls.hidden = false;
    const notes = ['Completed result playback'];
    if (data.geometry_source === 'current') notes.push('Older run: using current workspace geometry');
    if (data.missing_channels.length) notes.push(`Unavailable channels: ${data.missing_channels.join(', ')}. Missing platform motion uses zero.`);
    playbackMessage.textContent = notes.join(' · ');
    renderPlaybackFrame();
    return data;
  } catch (error) {
    if (generation === playbackGeneration) playbackMessage.textContent = `Playback unavailable: ${error.message}`;
  }
}

playbackPlay.onclick = () => {
  if (!playback) return;
  if (playback.playing) playback.pause(); else playback.play();
  lastPlaybackFrame = null;
  renderPlaybackFrame();
};
document.querySelector('#playbackReplay').onclick = () => {
  if (!playback) return;
  playback.seek(playbackData.timestamps[0]);
  playback.play();
  lastPlaybackFrame = null;
  renderPlaybackFrame();
};
playbackSeek.oninput = () => {
  if (!playback) return;
  playback.seek(Number(playbackSeek.value));
  lastPlaybackFrame = null;
  renderPlaybackFrame();
};
playbackSpeed.onchange = () => playback?.setSpeed(Number(playbackSpeed.value));
document.addEventListener('visibilitychange', () => { lastPlaybackFrame = null; });

function inputValue(parameter, input) {
  if (parameter.kind === 'boolean') return input.value === 'true';
  if (parameter.kind === 'integer') return Number.parseInt(input.value, 10);
  if (parameter.kind === 'number') return Number.parseFloat(input.value);
  return input.value;
}

function appendParameterRow(container, parameter) {
  const editableKeyword = parameter.key === 'FileName_BTS';
  const row = document.createElement('article');
  row.className = `parameter${parameter.kind === 'keyword' && !editableKeyword ? ' parameter-keyword' : ''}${parameter.reference ? ' parameter-reference' : ''}`;
  const main = document.createElement('div');
  main.className = 'parameter-main';

  const markerSlot = document.createElement('span');
  markerSlot.className = 'parameter-marker-slot';

  const controlId = `parameter-${++parameterControlSequence}`;
  const labelWrap = document.createElement('span');
  labelWrap.className = 'parameter-label';
  const label = document.createElement('label');
  label.htmlFor = controlId;
  label.textContent = parameter.key;
  label.title = parameter.description || parameter.key;
  const line = document.createElement('small');
  line.textContent = `L${parameter.line}`;
  labelWrap.append(label, line);

  let input;
  if (parameter.kind === 'boolean') {
    input = document.createElement('select');
    ['true', 'false'].forEach(value => input.add(new Option(value, value)));
    input.value = String(parameter.value);
  } else {
    input = document.createElement('input');
    input.value = String(parameter.value ?? '');
  }
  input.id = controlId;
  input.dataset.key = parameter.key;
  input.dataset.original = String(parameter.value);
  input.dataset.kind = editableKeyword ? 'string' : parameter.kind;
  input.title = parameter.description || parameter.key;
  if (parameter.kind === 'keyword' && !editableKeyword) {
    input.readOnly = true;
    input.setAttribute('aria-readonly', 'true');
  } else {
    input.dataset.editable = 'true';
  }

  const valueWrap = document.createElement('span');
  valueWrap.className = 'parameter-value';
  valueWrap.append(input);
  if (parameter.reference) {
    const reference = document.createElement('span');
    reference.className = 'parameter-kind';
    reference.textContent = 'LINK';
    reference.title = `Linked input: ${parameter.reference}`;
    valueWrap.append(reference);
  } else if (parameter.kind === 'keyword' && !editableKeyword) {
    const keyword = document.createElement('span');
    keyword.className = 'parameter-kind';
    keyword.textContent = 'AUTO';
    valueWrap.append(keyword);
  }

  main.append(markerSlot, labelWrap, valueWrap);
  row.append(main);

  if (parameter.description) {
    const instructionId = `${controlId}-instruction`;
    const marker = document.createElement('button');
    marker.type = 'button';
    marker.className = 'instruction-marker';
    marker.textContent = 'i';
    marker.title = `Show the instruction from source line ${parameter.line}`;
    marker.setAttribute('aria-label', `Show instruction for ${parameter.key}`);
    marker.setAttribute('aria-expanded', 'false');
    marker.setAttribute('aria-controls', instructionId);
    markerSlot.append(marker);

    const instruction = document.createElement('div');
    instruction.id = instructionId;
    instruction.className = 'parameter-instruction';
    instruction.hidden = true;
    const source = document.createElement('span');
    source.textContent = `SOURCE LINE ${parameter.line}`;
    const copy = document.createElement('p');
    copy.textContent = parameter.description;
    instruction.append(source, copy);
    row.append(instruction);

    marker.onclick = () => {
      const expanded = marker.getAttribute('aria-expanded') === 'true';
      marker.setAttribute('aria-expanded', String(!expanded));
      marker.setAttribute('aria-label', `${expanded ? 'Show' : 'Hide'} instruction for ${parameter.key}`);
      instruction.hidden = expanded;
      row.classList.toggle('instruction-open', !expanded);
    };
  }

  container.append(row);
}

function renderParsedFile(container, node, payload) {
  container.innerHTML = '';
  const summary = document.createElement('div');
  summary.className = 'parsed-summary';
  const summaryCopy = document.createElement('span');
  summaryCopy.textContent = `${payload.parameters.length} parsed record${payload.parameters.length === 1 ? '' : 's'}`;
  const summaryMeta = document.createElement('span');
  summaryMeta.textContent = `${payload.line_count} lines · ${(payload.size / 1024).toFixed(1)} KB`;
  summary.append(summaryCopy, summaryMeta);
  container.append(summary);

  payload.parameters.forEach(parameter => appendParameterRow(container, parameter));
  const editable = payload.parameters.filter(parameter => parameter.kind !== 'keyword' || parameter.key === 'FileName_BTS');
  if (!payload.parameters.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No scalar records were recognized in this linked input.';
    container.append(empty);
  }

  container.dataset.mtime = payload.mtime_ns;
  container.dataset.loaded = 'true';
  if (node.path === activeWind?.inflow_file) {
    const windHost = document.createElement('div');
    windHost.dataset.windHost = 'true';
    container.append(windHost);
    renderTurbSimSection(windHost);
  }
  if (!editable.length) return;

  const actions = document.createElement('div');
  actions.className = 'file-actions';
  const reset = document.createElement('button');
  reset.type = 'button'; reset.textContent = 'RESET';
  const save = document.createElement('button');
  save.type = 'button'; save.className = 'primary'; save.textContent = 'SAVE CHANGES';
  actions.append(reset, save);
  container.append(actions);
  reset.onclick = () => container.querySelectorAll('[data-editable="true"]').forEach(input => { input.value = input.dataset.original; });
  save.onclick = async () => {
    const controls = [...container.querySelectorAll('[data-editable="true"]')];
    const changed = controls.filter(input => input.value !== input.dataset.original);
    if (!changed.length) return notify('No changes to save');
    const updates = Object.fromEntries(changed.map(input => [input.dataset.key, inputValue({ kind: input.dataset.kind }, input)]));
    save.disabled = true;
    try {
      const saved = await request(`${workspaceUrl('/file')}?path=${encodeURIComponent(node.path)}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ updates, expected_mtime_ns: container.dataset.mtime }),
      });
      container.dataset.mtime = saved.mtime_ns;
      parameterCache.delete(`${activeWorkspace?.workspace_id}:${node.path}`);
      changed.forEach(input => { input.dataset.original = input.value; });
      if (node.path === activeWind?.inflow_file) {
        activeWind = await request(workspaceUrl('/wind'));
        renderParsedFile(container, node, saved);
      } else if (node.path === activeWind?.selected_turbsim_input) {
        await refreshWindUI();
      }
      logMessage('INFO', `Saved ${changed.length} parameter${changed.length === 1 ? '' : 's'} in ${node.name}`);
      notify(`Saved ${changed.length} parameter${changed.length === 1 ? '' : 's'}`);
    } catch (error) {
      notify(error.message);
    } finally {
      save.disabled = false;
    }
  };
}

function windModeLabel(mode) {
  return ({ managed: 'MANAGED', external: 'EXTERNAL', unconfigured: 'SETUP REQUIRED' })[mode] || 'INACTIVE';
}

function renderBinaryWindLeaf(container) {
  if (!activeWind?.resolved_bts && !activeWind?.managed_bts) return;
  const leaf = document.createElement('div');
  leaf.className = `wind-binary ${activeWind.bts_exists ? 'available' : 'missing'}`;
  const icon = document.createElement('span');
  icon.className = 'file-node-icon'; icon.textContent = 'BTS';
  const copy = document.createElement('span'); copy.className = 'file-node-copy';
  const name = document.createElement('span'); name.className = 'file-node-name';
  const path = activeWind.resolved_bts || activeWind.managed_bts;
  name.textContent = path.split('/').pop(); name.title = path;
  const meta = document.createElement('span'); meta.className = 'file-node-meta';
  if (!activeWind.bts_exists) meta.textContent = 'MISSING · WILL GENERATE ON RUN';
  else if (activeWind.bts_stale) meta.textContent = `${(activeWind.bts_size / 1024).toFixed(1)} KB · STALE`;
  else meta.textContent = `${(activeWind.bts_size / 1024).toFixed(1)} KB · READY`;
  copy.append(name, meta); leaf.append(icon, copy); container.append(leaf);
}

function renderTurbSimSection(host) {
  host.innerHTML = '';
  if (!activeWind?.active) return;
  const section = document.createElement('section'); section.className = `wind-section mode-${activeWind.mode}`;
  const header = document.createElement('div'); header.className = 'wind-section-header';
  const title = document.createElement('span'); title.textContent = 'TURBSIM WIND FIELD';
  const badge = document.createElement('b'); badge.textContent = windModeLabel(activeWind.mode);
  header.append(title, badge);
  const message = document.createElement('p'); message.textContent = activeWind.message;
  const label = document.createElement('label'); label.textContent = 'MANAGED TURBSIM INPUT';
  const select = document.createElement('select');
  select.add(new Option('Select a workspace .in…', ''));
  activeWind.turbsim_inputs.forEach(candidate => select.add(new Option(candidate.path, candidate.path)));
  select.value = activeWind.selected_turbsim_input || '';
  select.disabled = running || activeWind.turbsim_inputs.length === 0;
  select.onchange = async () => {
    if (!select.value) return;
    select.disabled = true;
    try {
      activeWind = await request(workspaceUrl('/wind'), {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          turbsim_input: select.value,
          expected_inflow_mtime_ns: activeWind.inflow_mtime_ns,
        }),
      });
      parameterCache.delete(`${activeWorkspace?.workspace_id}:${activeWind.inflow_file}`);
      const details = host.closest('.file-node-results');
      if (details) {
        const inflowNode = activeModel.files.find(item => item.path === activeWind.inflow_file);
        const payload = await parametersFor(activeWind.inflow_file);
        renderParsedFile(details, inflowNode, payload);
      } else {
        renderTurbSimSection(host);
      }
      notify('Managed TurbSim input selected');
    } catch (error) {
      notify(error.message); renderTurbSimSection(host);
    }
  };
  section.append(header, message, label, select);
  renderBinaryWindLeaf(section);
  host.append(section);
}

async function refreshWindUI() {
  if (!activeWorkspace) return;
  activeWind = await request(workspaceUrl('/wind'));
  document.querySelectorAll('[data-wind-host="true"]').forEach(renderTurbSimSection);
}

async function toggleFile(node, button, details) {
  const expanded = button.getAttribute('aria-expanded') === 'true';
  button.setAttribute('aria-expanded', String(!expanded));
  button.classList.toggle('active', !expanded);
  details.hidden = expanded;
  if (expanded) return;

  activeNode = node;
  if (details.dataset.loaded === 'true' || details.dataset.loading === 'true') return;
  details.dataset.loading = 'true';
  details.innerHTML = '<div class="loading">PARSING INPUT RECORDS…</div>';
  try {
    const payload = await parametersFor(node.path);
    if (!details.isConnected) return;
    renderParsedFile(details, node, payload);
  } catch (error) {
    if (!details.isConnected) return;
    details.innerHTML = `<div class="error">${safeText(error.message)}</div>`;
  } finally {
    delete details.dataset.loading;
  }
}

function renderFileTree(files) {
  fileTree.innerHTML = '';
  activeNode = files[0] || null;
  files.forEach((node, index) => {
    const branch = document.createElement('div');
    branch.className = 'file-branch';
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'file-node';
    button.setAttribute('aria-expanded', 'false');
    const icon = document.createElement('span');
    icon.className = 'file-node-icon'; icon.textContent = node.name.split('.').pop().slice(0, 3).toUpperCase();
    const copy = document.createElement('span'); copy.className = 'file-node-copy';
    const name = document.createElement('span'); name.className = 'file-node-name'; name.textContent = node.name; name.title = node.path;
    const meta = document.createElement('span'); meta.className = 'file-node-meta';
    meta.textContent = `${node.source_kind === 'turbsim' ? 'TURBSIM · ' : ''}${node.parameter_count} PARSED · ${(node.size / 1024).toFixed(1)} KB`;
    const details = document.createElement('div');
    details.id = `linked-file-${index}`;
    details.className = 'file-node-results';
    details.hidden = true;
    button.setAttribute('aria-controls', details.id);
    copy.append(name, meta);
    button.append(icon, copy);
    button.onclick = () => toggleFile(node, button, details);
    branch.append(button, details);
    fileTree.append(branch);
  });
}

async function loadWorkspace(workspaceId) {
  resultsPanel.reset(workspaceId);
  resetPlayback();
  workspaceForm.inert = true;
  studyGeneration++;
  const generation = playbackGeneration;
  fileTree.innerHTML = '<div class="loading">WALKING REFERENCED OPENFAST FILES…</div>';
  try {
    const [model, wind] = await Promise.all([
      request(`/api/workspaces/${encodeURIComponent(workspaceId)}/model`),
      request(`/api/workspaces/${encodeURIComponent(workspaceId)}/wind`),
    ]);
    if (generation !== playbackGeneration) return;
    const workspaceChanged = activeWorkspace?.workspace_id !== model.workspace.workspace_id;
    activeWorkspace = model.workspace;
    simulationPanel.setWorkspace(activeWorkspace.workspace_id);
    activeModel = model;
    activeWind = wind;
    parameterCache.clear();

    const modelName = model.entry.split('/').pop();
    activeModelName.textContent = modelName;
    activeModelName.title = `${activeWorkspace.name}\nSource: ${activeWorkspace.source_path}`;
    document.querySelector('#modelMode').textContent = 'LOCKED .FST';
    viewportModelLabel.textContent = activeWorkspace.name.toUpperCase();
    fileCount.textContent = `${model.files.length} FILES`;
    renderFileTree(model.files);
    if (workspaceChanged) resetStudyForm();
    workspaceForm.inert = false;
    rebuildGeometry(model.geometry);
    logMessage('INFO', `Loaded ${activeWorkspace.name}: ${model.files.length} input files from ${modelName}`);
    localStorage.setItem('mcfast-workspace', workspaceId);
    await Promise.all([loadStudies(), loadRunHistory()]);
    if (generation === playbackGeneration && runHistorySelect.value) await showHistoricalRun(runHistorySelect.value);
  } catch (error) {
    if (generation !== playbackGeneration) return;
    fileTree.innerHTML = `<div class="error">${error.message}</div>`;
    setStatus('error', 'API ERROR');
  } finally {
    if (generation === playbackGeneration) workspaceForm.inert = !activeModel;
  }
}

async function parametersFor(path) {
  const cacheKey = `${activeWorkspace?.workspace_id}:${path}`;
  if (!parameterCache.has(cacheKey)) {
    parameterCache.set(cacheKey, request(`${workspaceUrl('/file')}?path=${encodeURIComponent(path)}`));
  }
  try {
    return await parameterCache.get(cacheKey);
  } catch (error) {
    parameterCache.delete(cacheKey);
    throw error;
  }
}

function updateEmptyVariableState() {
  emptyVariableBtn.hidden = variableList.children.length > 0;
}

function selectedParameter(row) {
  const option = row.querySelector('[data-role="parameter"]').selectedOptions[0];
  let originalValue = null;
  try { originalValue = JSON.parse(option?.dataset.value ?? 'null'); } catch { originalValue = null; }
  return { kind: option?.dataset.kind || '', originalValue };
}

function collectVariables() {
  return [...variableList.querySelectorAll('.variable-row')].map(row => {
    const parameter = row.querySelector('[data-role="parameter"]');
    const selected = selectedParameter(row);
    return {
      id: row.dataset.variableId,
      name: row.querySelector('[data-role="name"]').value.trim(),
      file: row.querySelector('[data-role="file"]').value,
      key: parameter.value,
      kind: selected.kind,
      originalValue: selected.originalValue,
    };
  });
}

function updateCaseControls() {
  const selectedCount = caseRows.filter(row => row.selected).length;
  emptyCaseBtn.hidden = caseRows.length > 0;
  clearCasesBtn.disabled = caseRows.length === 0;
  clearSelectedCasesBtn.disabled = selectedCount === 0;
  selectAllCases.checked = caseRows.length > 0 && selectedCount === caseRows.length;
  selectAllCases.indeterminate = selectedCount > 0 && selectedCount < caseRows.length;
}

function caseCell(variable, value, row) {
  let control;
  if (variable.kind === 'boolean') {
    control = document.createElement('select');
    control.add(new Option('True', 'true'));
    control.add(new Option('False', 'false'));
    control.value = String(value).toLowerCase() === 'true' ? 'true' : 'false';
    control.onchange = () => { row.values[variable.id] = control.value === 'true'; };
  } else {
    control = document.createElement('input');
    control.type = variable.kind === 'number' || variable.kind === 'integer' ? 'number' : 'text';
    if (variable.kind === 'number') control.step = 'any';
    if (variable.kind === 'integer') control.step = '1';
    control.value = value ?? '';
    control.oninput = () => { row.values[variable.id] = control.value; control.classList.remove('invalid'); };
  }
  control.setAttribute('aria-label', `${variable.name || variable.key} value`);
  return control;
}

function renderCaseTable() {
  const variables = collectVariables();
  caseTableHead.querySelectorAll('th[data-variable-id]').forEach(cell => cell.remove());
  variables.forEach(variable => {
    const heading = document.createElement('th');
    heading.dataset.variableId = variable.id;
    heading.textContent = variable.name || variable.key || 'Unnamed variable';
    heading.title = `${variable.file} · ${variable.key || 'Select a parameter'}`;
    caseTableHead.append(heading);
  });
  caseTableBody.innerHTML = '';
  caseRows.forEach((row, index) => {
    const tr = document.createElement('tr');
    tr.dataset.caseId = row.id;
    const selectCell = document.createElement('td');
    selectCell.className = 'case-select-cell';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = row.selected; checkbox.setAttribute('aria-label', `Select case ${index + 1}`);
    checkbox.onchange = () => { row.selected = checkbox.checked; updateCaseControls(); };
    selectCell.append(checkbox);
    const numberCell = document.createElement('td');
    numberCell.className = 'case-number-cell'; numberCell.textContent = index + 1;
    tr.append(selectCell, numberCell);
    variables.forEach(variable => {
      const td = document.createElement('td');
      td.dataset.variableId = variable.id;
      td.append(caseCell(variable, row.values[variable.id], row));
      tr.append(td);
    });
    caseTableBody.append(tr);
  });
  caseTable.style.setProperty('--case-columns', Math.max(1, variables.length));
  updateCaseControls();
}

function addCase(values = null) {
  const variables = collectVariables();
  if (!variables.length || variables.some(variable => !variable.key)) return notify('Complete a variable binding first');
  const previous = caseRows.at(-1);
  const rowValues = {};
  variables.forEach(variable => {
    rowValues[variable.id] = values && Object.hasOwn(values, variable.id)
      ? values[variable.id]
      : previous ? previous.values[variable.id] : variable.originalValue;
  });
  caseRows.push({ id: `case-${++caseSequence}`, selected: false, values: rowValues });
  renderCaseTable();
}

function resetCaseColumn(row) {
  const variableId = row.dataset.variableId;
  const { originalValue } = selectedParameter(row);
  caseRows.forEach(caseRow => { caseRow.values[variableId] = originalValue; });
  renderCaseTable();
}

async function populateParameterSelect(row, preferredKey = '') {
  const fileSelect = row.querySelector('[data-role="file"]');
  const parameterSelect = row.querySelector('[data-role="parameter"]');
  parameterSelect.disabled = true;
  parameterSelect.innerHTML = '<option>Loading parameters…</option>';
  try {
    const payload = await parametersFor(fileSelect.value);
    const parameters = payload.parameters.filter(parameter => parameter.kind !== 'keyword');
    parameterSelect.innerHTML = '';
    if (!parameters.length) {
      parameterSelect.add(new Option('No scalar parameters found', ''));
      return;
    }
    parameters.forEach(parameter => {
      const value = typeof parameter.value === 'string' ? `“${parameter.value}”` : String(parameter.value);
      const option = new Option(`${parameter.key}  =  ${value}`, parameter.key);
      option.dataset.kind = parameter.kind;
      option.dataset.value = JSON.stringify(parameter.value);
      option.title = parameter.description || parameter.key;
      parameterSelect.add(option);
    });
    if (preferredKey && parameters.some(parameter => parameter.key === preferredKey)) {
      parameterSelect.value = preferredKey;
    } else {
      parameterSelect.value = parameters[0].key;
    }
    parameterSelect.disabled = false;
  } catch (error) {
    parameterSelect.innerHTML = `<option>${error.message}</option>`;
  }
}

function handleParameterChange(row) {
  if (!row.isConnected) return;
  const parameterSelect = row.querySelector('[data-role="parameter"]');
  const alias = row.querySelector('[data-role="name"]');
  if (!alias.dataset.edited) alias.value = parameterSelect.value;
  resetCaseColumn(row);
}

function addVariable(preferredFile = null, preferredKey = '') {
  if (!activeModel?.files?.length) return notify('Select a model first');
  const row = document.createElement('div');
  row.className = 'variable-row';
  row.dataset.variableId = `variable-${++variableSequence}`;

  const name = document.createElement('input');
  name.dataset.role = 'name'; name.placeholder = 'variable_name'; name.setAttribute('aria-label', 'Variable name');
  name.oninput = () => { name.dataset.edited = 'true'; renderCaseTable(); };
  const file = document.createElement('select');
  file.dataset.role = 'file'; file.setAttribute('aria-label', 'Input file');
  activeModel.files.forEach(node => file.add(new Option(
    node.source_kind === 'turbsim' ? `${node.name} · TurbSim` : node.name,
    node.path,
  )));
  const desiredFile = preferredFile && activeModel.files.some(node => node.path === preferredFile)
    ? preferredFile : activeModel.files[0].path;
  file.value = desiredFile;
  const parameter = document.createElement('select');
  parameter.dataset.role = 'parameter'; parameter.setAttribute('aria-label', 'Exact parameter');
  const remove = document.createElement('button');
  remove.type = 'button'; remove.className = 'remove-variable'; remove.textContent = '×'; remove.title = 'Remove variable'; remove.setAttribute('aria-label', 'Remove variable');
  remove.onclick = () => {
    caseRows.forEach(caseRow => { delete caseRow.values[row.dataset.variableId]; });
    row.remove(); updateEmptyVariableState(); renderCaseTable();
  };
  file.onchange = async () => { name.dataset.edited = ''; await populateParameterSelect(row); handleParameterChange(row); };
  parameter.onchange = () => handleParameterChange(row);
  for (const [control, title] of [[name, 'VARIABLE NAME'], [file, 'INPUT FILE'], [parameter, 'EXACT PARAMETER']]) {
    const label = document.createElement('label');
    label.className = 'field';
    const caption = document.createElement('span');
    caption.textContent = title;
    label.append(caption, control);
    row.append(label);
  }
  row.append(remove);
  variableList.append(row);
  updateEmptyVariableState();
  row.parameterReady = populateParameterSelect(row, preferredKey).then(() => handleParameterChange(row));
  return row;
}

function resetStudyForm() {
  studyDirty = false;
  studyGeneration++;
  createWorkspaceBtn.disabled = false;
  studyWorkspaceId = activeWorkspace?.workspace_id ?? null;
  editingStudyId = null;
  studySelect.value = '';
  variableList.innerHTML = '';
  caseRows = [];
  workspaceResult.hidden = true;
  modalFootnote.hidden = false;
  csvInput.value = ''; csvStatus.hidden = true; csvStatus.innerHTML = '';
  const stem = activeModel?.entry?.split('/').pop().replace(/\.fst$/i, '') || 'OpenFAST';
  workspaceName.value = `${stem} variable study`;
  workspaceSource.textContent = activeModel?.entry?.split('/').pop() || 'No model selected';
  workspaceSource.title = activeModel?.entry || '';
  const turbsimCount = activeModel?.files?.filter(node => node.source_kind === 'turbsim').length || 0;
  const linkedCount = (activeModel?.files?.length || 0) - turbsimCount;
  workspaceSourceMeta.textContent = `${linkedCount} linked input${linkedCount === 1 ? '' : 's'}${turbsimCount ? ` + ${turbsimCount} TurbSim input${turbsimCount === 1 ? '' : 's'}` : ''} in ${activeWorkspace?.name || 'this workspace'}.`;
  addVariable(activeNode?.path || activeModel?.files?.[0]?.path);
  renderCaseTable();
  createWorkspaceBtn.querySelector('span').textContent = 'SAVE STUDY';
}

async function loadStudies() {
  if (!activeWorkspace) return;
  const workspaceId = activeWorkspace.workspace_id;
  const payload = await request(workspaceUrl('/studies'));
  if (workspaceId !== activeWorkspace?.workspace_id) return;
  savedStudies = payload.studies;
  studySelect.innerHTML = '<option value="">New study</option>';
  savedStudies.forEach(study => studySelect.add(new Option(
    `${study.name} · ${study.sample_count} cases`, study.study_id,
  )));
}

async function openSavedStudy(studyId) {
  if (!studyId) return resetStudyForm();
  const generation = ++studyGeneration;
  const workspaceId = activeWorkspace.workspace_id;
  const study = await request(workspaceUrl(`/studies/${encodeURIComponent(studyId)}`));
  if (generation !== studyGeneration || workspaceId !== activeWorkspace?.workspace_id) return;
  editingStudyId = study.study_id;
  studyWorkspaceId = activeWorkspace.workspace_id;
  workspaceName.value = study.name;
  workspaceSource.textContent = activeModel.entry.split('/').pop();
  const turbsimCount = activeModel.files.filter(node => node.source_kind === 'turbsim').length;
  const linkedCount = activeModel.files.length - turbsimCount;
  workspaceSourceMeta.textContent = `${linkedCount} linked input${linkedCount === 1 ? '' : 's'}${turbsimCount ? ` + ${turbsimCount} TurbSim input${turbsimCount === 1 ? '' : 's'}` : ''} in ${activeWorkspace.name}.`;
  variableList.innerHTML = '';
  caseRows = [];
  for (const variable of study.variables) {
    const row = addVariable(variable.file, variable.key);
    if (!row) continue;
    await row.parameterReady;
    if (generation !== studyGeneration || workspaceId !== activeWorkspace?.workspace_id) return;
    row.querySelector('[data-role="name"]').value = variable.name;
    row.querySelector('[data-role="name"]').dataset.edited = 'true';
  }
  const variables = collectVariables();
  studyDirty = false;
  caseRows = study.samples.map(sample => ({
    id: `case-${++caseSequence}`,
    selected: false,
    values: Object.fromEntries(variables.map(variable => [variable.id, sample[variable.name]])),
  }));
  csvInput.value = ''; csvStatus.hidden = true; csvStatus.innerHTML = '';
  renderCaseTable();
  workspaceResult.hidden = false;
  modalFootnote.hidden = true;
  workspaceResult.innerHTML = `<span>● SAVED</span><small>${study.variables.length} variables · ${study.samples.length} cases</small><a href="${workspaceUrl(`/studies/${encodeURIComponent(study.study_id)}/download`)}" download>DOWNLOAD JSON</a>`;
  createWorkspaceBtn.querySelector('span').textContent = 'UPDATE STUDY';
}

function collectCases(variables) {
  const samples = [];
  for (let rowIndex = 0; rowIndex < caseRows.length; rowIndex += 1) {
    const caseRow = caseRows[rowIndex];
    const sample = {};
    for (const variable of variables) {
      const value = caseRow.values[variable.id];
      let normalized = value;
      let valid = value !== null && value !== undefined && String(value).trim() !== '';
      if (valid && variable.kind === 'number') {
        normalized = Number(value); valid = Number.isFinite(normalized);
      } else if (valid && variable.kind === 'integer') {
        normalized = Number(value); valid = Number.isInteger(normalized);
      } else if (valid && variable.kind === 'boolean') {
        valid = typeof value === 'boolean';
      }
      const control = caseTableBody.querySelector(`tr[data-case-id="${caseRow.id}"] td[data-variable-id="${variable.id}"] input, tr[data-case-id="${caseRow.id}"] td[data-variable-id="${variable.id}"] select`);
      control?.classList.toggle('invalid', !valid);
      if (!valid) {
        notify(`Complete ${variable.name} in case row ${rowIndex + 1}`);
        return null;
      }
      sample[variable.name] = normalized;
    }
    samples.push(sample);
  }
  return samples;
}

async function saveStudy(event) {
  event.preventDefault();
  if (!activeWorkspace || workspaceForm.inert) return;
  const generation = studyGeneration;
  const variables = collectVariables();
  if (!workspaceName.value.trim()) return notify('Enter a study name');
  if (!variables.length) return notify('Add at least one variable');
  if (variables.some(variable => !variable.name || !variable.key)) return notify('Complete every variable binding');
  if (new Set(variables.map(variable => variable.name)).size !== variables.length) return notify('Variable names must be unique');
  if (!caseRows.length) return notify('Add at least one complete case');
  const samples = collectCases(variables);
  if (!samples) return;
  createWorkspaceBtn.disabled = true;
  createWorkspaceBtn.querySelector('span').textContent = 'SAVING…';
  try {
    const path = editingStudyId ? `/studies/${encodeURIComponent(editingStudyId)}` : '/studies';
    const result = await request(workspaceUrl(path), {
      method: editingStudyId ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: workspaceName.value.trim(),
        variables: variables.map(({ id, kind, originalValue, ...variable }) => variable), samples,
      }),
    });
    if (generation !== studyGeneration) return;
    studyDirty = false;
    editingStudyId = result.study_id;
    await loadStudies();
    if (generation !== studyGeneration) return;
    studySelect.value = editingStudyId;
    workspaceResult.hidden = false; modalFootnote.hidden = true;
    workspaceResult.innerHTML = `<span>● SAVED</span><small>${result.variable_count} variables · ${result.sample_count} cases</small><a href="${safeText(result.download_url)}" download>DOWNLOAD JSON</a>`;
    logMessage('INFO', `Saved variable study ${result.study_id} with ${result.sample_count} cases`);
    notify('Variable study saved');
  } catch (error) {
    notify(error.message);
  } finally {
    if (generation === studyGeneration) {
      createWorkspaceBtn.disabled = false;
      createWorkspaceBtn.querySelector('span').textContent = editingStudyId ? 'UPDATE STUDY' : 'SAVE STUDY';
    }
  }
}

function logMessage(level, message, simTime = 0) {
  if (running) return;
  const existing = consoleOutput.textContent.trim();
  consoleOutput.textContent = `${existing ? `${existing}\n` : ''}[${fmtTime(simTime)}] ${level.padEnd(5)} ${message}`;
  consoleOutput.scrollTop = consoleOutput.scrollHeight;
}

function toggleDrawer() {
  const open = resultsDrawer.classList.toggle('open');
  document.querySelector('#drawerHeader').setAttribute('aria-expanded', String(open));
}

function delay(milliseconds) {
  return new Promise(resolve => window.setTimeout(resolve, milliseconds));
}

function updateSimulationProgress(chunk) {
  const matches = [...chunk.matchAll(/Time:\s*([\d.]+)\s+of\s+([\d.]+)\s+seconds/gi)];
  const last = matches.at(-1);
  if (!last) return;
  const current = Number(last[1]);
  const total = Number(last[2]);
  simClock.textContent = fmtTime(current);
  if (total > 0) progressFill.style.width = `${Math.min(100, (current / total) * 100)}%`;
}

function renderArtifacts(artifacts) {
  runArtifacts.innerHTML = '';
  artifacts.forEach(artifact => {
    const link = document.createElement('a');
    link.href = artifact.url;
    link.download = artifact.name;
    link.textContent = artifact.name;
    const size = document.createElement('span');
    size.textContent = `${(artifact.bytes / 1024).toFixed(1)} KB`;
    link.append(size);
    runArtifacts.append(link);
  });
  runArtifacts.hidden = artifacts.length === 0;
}

function formatRunLabel(run) {
  const timestamp = run.started_at ? new Date(run.started_at).toLocaleString() : 'Current session';
  const sample = run.study_name ? ` · ${run.study_name} · sample ${run.sample_index + 1}` : '';
  return `${timestamp}${sample} · ${run.status.toUpperCase()}`;
}

async function loadRunHistory(selectedRunId = '') {
  if (!activeWorkspace) return;
  const workspaceId = activeWorkspace.workspace_id;
  const payload = await request(workspaceUrl('/runs'));
  if (activeWorkspace?.workspace_id !== workspaceId) return;
  runHistorySelect.innerHTML = '';
  if (!payload.runs.length) {
    runHistorySelect.add(new Option('NO RUNS', ''));
    runHistorySelect.disabled = true;
    resultsPanel.setRuns(runHistorySelect);
    return;
  }
  runHistorySelect.disabled = false;
  payload.runs.forEach(run => runHistorySelect.add(new Option(formatRunLabel(run), run.run_id)));
  runHistorySelect.value = selectedRunId && payload.runs.some(run => run.run_id === selectedRunId)
    ? selectedRunId : payload.runs[0].run_id;
  resultsPanel.setRuns(runHistorySelect, running);
}

async function showHistoricalRun(runId) {
  if (!runId || running) return;
  resetPlayback();
  const generation = playbackGeneration;
  const state = await request(workspaceUrl(`/runs/${encodeURIComponent(runId)}?offset=0`));
  if (generation !== playbackGeneration) return;
  currentRunId = runId;
  consoleOutput.textContent = state.console || 'No console output was saved for this run.';
  renderArtifacts(state.artifacts);
  const succeeded = state.status === 'completed';
  runBadge.textContent = state.status.toUpperCase();
  runBadge.className = `fault-badge ${succeeded ? 'complete' : state.status === 'running' ? 'running' : 'has-fault'}`;
  resultsDrawer.classList.add('open');
  document.querySelector('#drawerHeader').setAttribute('aria-expanded', 'true');
  const playbackLoad = loadPlayback(runId);
  await Promise.all([playbackLoad, resultsPanel.load(activeWorkspace.workspace_id, runId, playbackLoad)]);
}

async function runSimulation() {
  if (running || !activeModel || !activeWorkspace) return;
  resetPlayback('Playback becomes available when the run completes.');
  running = true;
  workspaceSelect.disabled = true;
  runHistorySelect.disabled = true;
  resultsPanel.setRuns(runHistorySelect, true);
  runBtn.disabled = true; runBtnLabel.textContent = 'RUNNING…'; progressTrack.classList.add('visible'); progressFill.style.width = '0%';
  document.querySelectorAll('.wind-section > select').forEach(select => { select.disabled = true; });
  resultsDrawer.classList.add('open'); document.querySelector('#drawerHeader').setAttribute('aria-expanded', 'true');
  runBadge.textContent = 'STARTING'; runBadge.className = 'fault-badge running';
  runArtifacts.hidden = true; runArtifacts.innerHTML = '';
  consoleOutput.textContent = 'Starting OpenFAST run pipeline…\n';
  setStatus('running', 'RUNNING');
  try {
    const started = await request(workspaceUrl('/runs'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    currentRunId = started.run_id;
    let offset = 0;
    let state = started;
    consoleOutput.textContent = '';
    while (state.status === 'queued' || state.status === 'running') {
      state = await request(workspaceUrl(`/runs/${encodeURIComponent(currentRunId)}?offset=${offset}`));
      if (state.console) {
        consoleOutput.append(document.createTextNode(state.console));
        consoleOutput.scrollTop = consoleOutput.scrollHeight;
        updateSimulationProgress(state.console);
      }
      offset = state.next_offset;
      const phase = state.phase || state.status;
      runBadge.textContent = phase.toUpperCase();
      runBtnLabel.textContent = phase === 'turbsim' ? 'GENERATING WIND…' : 'RUNNING…';
      setStatus('running', phase === 'turbsim' ? 'TURBSIM' : 'RUNNING');
      renderArtifacts(state.artifacts);
      if (state.status === 'queued' || state.status === 'running') await delay(250);
    }
    const finalState = await request(workspaceUrl(`/runs/${encodeURIComponent(currentRunId)}?offset=${offset}`));
    if (finalState.console) consoleOutput.append(document.createTextNode(finalState.console));
    renderArtifacts(finalState.artifacts);
    const succeeded = finalState.status === 'completed';
    runBadge.textContent = succeeded ? 'COMPLETE' : 'FAILED';
    runBadge.className = `fault-badge ${succeeded ? 'complete' : 'has-fault'}`;
    progressFill.style.width = succeeded ? '100%' : progressFill.style.width;
    setStatus(succeeded ? 'complete' : 'error', succeeded ? 'COMPLETE' : 'FAILED');
    if (!succeeded && finalState.error) consoleOutput.append(document.createTextNode(`\n${finalState.error}\n`));
    notify(succeeded ? `Run saved: ${currentRunId}` : `OpenFAST exited with code ${finalState.return_code ?? 'unknown'}`);
    await Promise.all([loadRunHistory(currentRunId), refreshWindUI()]);
    await loadPlayback(currentRunId);
  } catch (error) {
    runBadge.textContent = 'FAILED'; runBadge.className = 'fault-badge has-fault';
    setStatus('error', 'RUN FAILED');
    consoleOutput.append(document.createTextNode(`\nmcFAST error: ${error.message}\n`));
    notify(error.message);
  } finally {
    workspaceSelect.disabled = false;
    runHistorySelect.disabled = !runHistorySelect.value;
    resultsPanel.setRuns(runHistorySelect);
    if (currentRunId) await resultsPanel.load(activeWorkspace.workspace_id, currentRunId, Promise.resolve(playbackData));
    running = false; runBtn.disabled = false; runBtnLabel.textContent = 'RUN AGAIN'; progressTrack.classList.remove('visible');
    document.querySelectorAll('.wind-section > select').forEach(select => { select.disabled = activeWind?.turbsim_inputs?.length === 0; });
  }
}

featureButtons.forEach((button, index) => {
  button.onclick = () => selectFeature(button.dataset.feature, true);
  button.onkeydown = event => {
    let next;
    if (event.key === 'ArrowDown') next = (index + 1) % featureButtons.length;
    if (event.key === 'ArrowUp') next = (index + featureButtons.length - 1) % featureButtons.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = featureButtons.length - 1;
    if (next !== undefined) { event.preventDefault(); featureButtons[next].focus(); }
  };
});
document.querySelector('#collapseBtn').onclick = () => { selectFeature(activeFeature, true); featureButtons.find(button => button.dataset.feature === activeFeature).focus(); };
document.querySelector('#drawerHeader').onclick = toggleDrawer;
document.querySelector('#drawerHeader').onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleDrawer(); } };
runBtn.onclick = runSimulation;
workspaceSelect.onchange = () => loadWorkspace(workspaceSelect.value);
themeToggle.onclick = () => applyTheme(document.documentElement.dataset.theme === 'light' ? 'dark' : 'light');
document.querySelector('#cancelWorkspaceBtn').onclick = () => { selectFeature(activeFeature, true); featureButtons[2].focus(); };
document.querySelector('#addVariableBtn').onclick = () => addVariable(activeNode?.path);
emptyVariableBtn.onclick = () => addVariable(activeNode?.path);
studySelect.onchange = () => openSavedStudy(studySelect.value).catch(error => notify(error.message));
document.querySelector('#newStudyBtn').onclick = resetStudyForm;
runHistorySelect.onclick = event => event.stopPropagation();
runHistorySelect.onkeydown = event => event.stopPropagation();
runHistorySelect.onchange = () => showHistoricalRun(runHistorySelect.value).catch(error => notify(error.message));
document.querySelector('#addCaseBtn').onclick = () => addCase();
emptyCaseBtn.onclick = () => addCase();
selectAllCases.onchange = () => {
  caseRows.forEach(row => { row.selected = selectAllCases.checked; });
  renderCaseTable();
};
clearSelectedCasesBtn.onclick = () => {
  caseRows = caseRows.filter(row => !row.selected);
  renderCaseTable();
};
clearCasesBtn.onclick = () => { caseRows = []; renderCaseTable(); };
document.querySelector('#csvChooseBtn').onclick = () => csvInput.click();
csvInput.onchange = async () => {
  const generation = studyGeneration;
  const importUrl = activeWorkspace ? workspaceUrl('/studies/csv-import') : null;
  if (!importUrl || workspaceForm.inert) return;
  const file = csvInput.files?.[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    csvInput.value = ''; return notify('CSV must be smaller than 10 MB');
  }
  const variables = collectVariables();
  if (!variables.length || variables.some(variable => !variable.name || !variable.key)) {
    csvInput.value = ''; return notify('Complete every variable binding before uploading CSV');
  }
  if (new Set(variables.map(variable => variable.name)).size !== variables.length) {
    csvInput.value = ''; return notify('Variable names must be unique');
  }
  try {
    const result = await request(importUrl, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        variables: variables.map(({ id, kind, originalValue, ...variable }) => variable),
        csv_text: await file.text(),
      }),
    });
    if (generation !== studyGeneration) return;
    if ((caseRows.length + result.samples.length) > 100000
      || (caseRows.length + result.samples.length) * variables.length > 1_000_000) {
      return notify('Appending this CSV would exceed the case-table limits');
    }
    result.samples.forEach(sample => {
      caseRows.push({
        id: `case-${++caseSequence}`,
        selected: false,
        values: Object.fromEntries(variables.map(variable => [variable.id, sample[variable.name]])),
      });
    });
    renderCaseTable();
    csvStatus.hidden = false;
    csvStatus.innerHTML = '';
    const summary = document.createElement('b');
    summary.textContent = `${file.name}: appended ${result.imported_count.toLocaleString()} case${result.imported_count === 1 ? '' : 's'}`;
    const detail = document.createElement('small');
    detail.textContent = result.skipped_count
      ? `${result.skipped_count.toLocaleString()} invalid row${result.skipped_count === 1 ? '' : 's'} skipped. ${result.errors.map(error => `Row ${error.row}, ${error.column}: ${error.message}`).join(' · ')}`
      : 'All non-blank rows were imported.';
    csvStatus.append(summary, detail);
    notify(result.imported_count ? 'CSV cases appended' : 'CSV contained no valid cases');
  } catch (error) {
    notify(error.message);
  } finally {
    if (generation === studyGeneration) csvInput.value = '';
  }
};
workspaceForm.addEventListener('submit', saveStudy);

function populateSources() {
  sourceSelect.innerHTML = '<option value="">Choose a discovered .fst…</option>';
  sourceModels.forEach(source => sourceSelect.add(new Option(source.name, source.source_path)));
  const umaine = sourceModels.find(source => source.name === 'IEA-15-240-RWT-UMaineSemi');
  if (umaine) sourceSelect.value = umaine.source_path;
}

async function refreshWorkspaceSelect(preferredId = '') {
  const payload = await request('/api/workspaces');
  workspaceSelect.innerHTML = '';
  if (!payload.workspaces.length) {
    workspaceSelect.add(new Option('No workspaces — import .fst', ''));
    workspaceSelect.disabled = true;
    activeWorkspace = null;
    activeModel = null;
    fileTree.innerHTML = `<div class="empty">${safeText(payload.onboarding || 'Import a local .fst project to begin.')}</div>`;
    runBtn.disabled = true;
    return;
  }
  workspaceSelect.disabled = false;
  payload.workspaces.forEach(workspace => workspaceSelect.add(new Option(workspace.name, workspace.workspace_id)));
  const saved = preferredId || localStorage.getItem('mcfast-workspace');
  workspaceSelect.value = payload.workspaces.some(workspace => workspace.workspace_id === saved)
    ? saved : payload.workspaces[0].workspace_id;
  runBtn.disabled = false;
  await loadWorkspace(workspaceSelect.value);
}

function openImportModal() {
  importForm.reset();
  populateSources();
  const selected = sourceModels.find(source => source.source_path === sourceSelect.value);
  sourcePath.value = selected?.source_path || '';
  importName.value = selected ? selected.name.replace(/-/g, ' ') : '';
  importModal.showModal();
  window.setTimeout(() => importName.focus(), 0);
}

sourceSelect.onchange = () => {
  sourcePath.value = sourceSelect.value;
  const selected = sourceModels.find(source => source.source_path === sourceSelect.value);
  if (selected && !importName.value.trim()) importName.value = selected.name.replace(/-/g, ' ');
};

importForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!importName.value.trim() || !sourcePath.value.trim()) return notify('Enter a name and .fst path');
  confirmImportBtn.disabled = true;
  confirmImportBtn.querySelector('span').textContent = 'IMPORTING…';
  try {
    const created = await request('/api/workspaces', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: importName.value.trim(), source_path: sourcePath.value.trim() }),
    });
    importModal.close();
    await refreshWorkspaceSelect(created.workspace_id);
    notify('Workspace project imported');
  } catch (error) {
    notify(error.message);
  } finally {
    confirmImportBtn.disabled = false;
    confirmImportBtn.querySelector('span').textContent = 'IMPORT WORKSPACE';
  }
});
document.querySelector('#importWorkspaceBtn').onclick = openImportModal;
document.querySelector('#importClose').onclick = () => importModal.close();
document.querySelector('#cancelImportBtn').onclick = () => importModal.close();

async function boot() {
  rebuildGeometry(undefined, 'Select a workspace to inspect geometry');
  try {
    const payload = await request('/api/sources');
    sourceModels = payload.sources;
    populateSources();
    setStatus('online', 'API ONLINE');
    await refreshWorkspaceSelect();
  } catch (error) {
    setStatus('error', 'API OFFLINE');
    fileTree.innerHTML = `<div class="error">${error.message}</div>`;
  }
}

async function updateStudyConsole(runId) {
  const workspaceId = activeWorkspace?.workspace_id;
  if (!workspaceId || currentRunId !== runId) return true;
  const state = await request(workspaceUrl(`/runs/${encodeURIComponent(runId)}?offset=0`));
  if (activeWorkspace?.workspace_id !== workspaceId || currentRunId !== runId) return true;
  resultsDrawer.classList.add('open');
  document.querySelector('#drawerHeader').setAttribute('aria-expanded', 'true');
  consoleOutput.textContent = state.console || 'Waiting for case console output…';
  runBadge.textContent = state.status.toUpperCase().replaceAll('_', ' ');
  runBadge.className = 'fault-badge running';
  renderArtifacts(state.artifacts);
  if (['completed', 'failed', 'cancelled', 'interrupted'].includes(state.status)) {
    await loadRunHistory(runId);
    await showHistoricalRun(runId);
    return true;
  }
  return false;
}

const simulationPanel = createSimulationPanel({
  request,
  hasUnsavedStudy: () => studyDirty,
  onResult: async (runId, state) => {
    await loadRunHistory(runId);
    if (['completed', 'failed', 'cancelled', 'interrupted'].includes(state)) await showHistoricalRun(runId);
    else {
      currentRunId = runId;
      resetPlayback('Playback becomes available when this case completes.');
      await updateStudyConsole(runId);
    }
  },
  onProgress: updateStudyConsole,
  notify,
});

requestAnimationFrame(renderTelemetry);
applyTheme(document.documentElement.dataset.theme, false);
renderCaseTable();
boot();


workspaceForm.addEventListener('input', () => { studyDirty = true; });
workspaceForm.addEventListener('change', event => { if (event.target !== studySelect) studyDirty = true; });
workspaceForm.addEventListener('click', event => {
  if (event.target.closest('#addVariableBtn, #emptyVariableBtn, #addCaseBtn, #emptyCaseBtn, #clearCasesBtn, #clearSelectedCasesBtn, .remove-variable')) studyDirty = true;
});
