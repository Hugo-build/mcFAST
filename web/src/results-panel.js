import { intervalSeries, seriesCSV, reducePoints, requestGuard, channelCache } from './results-data.js';

const NS = 'http://www.w3.org/2000/svg';
const svgNode = (tag, attrs = {}, text = '') => {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  node.textContent = text;
  return node;
};
const format = n => Number(n.toPrecision(5)).toString();

export function createResultsPanel({ request, selectRun }) {
  const panel = document.querySelector('#resultsPanel');
  const toggle = document.querySelector('#resultsToggle');
  const runs = panel.querySelector('#resultsRun');
  const message = panel.querySelector('#resultsMessage');
  const search = panel.querySelector('#channelSearch');
  const list = panel.querySelector('#resultsChannels');
  const graphs = panel.querySelector('#resultsGraphs');
  const controls = panel.querySelector('#resultsControls');
  const startInput = panel.querySelector('#resultsStart');
  const endInput = panel.querySelector('#resultsEnd');
  const csv = panel.querySelector('#resultsCSV');
  const png = panel.querySelector('#resultsPNG');
  const retry = panel.querySelector('#resultsRetry');
  const guard = requestGuard();
  const seriesGuard = requestGuard();
  let workspace = '', run = '', metadata = null, selected = [], data = null, start = 0, end = 1;
  const cache = channelCache();
  let pending = null;
  const cancel = () => { pending?.abort(); pending = null; };
  const release = () => {
    if (workspace && run) request(`/api/workspaces/${encodeURIComponent(workspace)}/runs/${encodeURIComponent(run)}/results/cache`, { method: 'DELETE' }).catch(() => {});
  };
  let note = '';

  function open(value) {
    panel.hidden = !value;
    toggle.classList.toggle('active', value);
    toggle.setAttribute('aria-expanded', String(value));
    toggle.setAttribute('aria-pressed', String(value));
  }
  toggle.onclick = () => open(panel.hidden);
  panel.querySelector('#resultsClose').onclick = () => { open(false); toggle.focus(); };
  panel.onkeydown = event => { if (event.key === 'Escape') { open(false); toggle.focus(); } };
  runs.onchange = () => selectRun(runs.value);

  function status(text, canRetry = false) {
    message.textContent = text;
    retry.hidden = !canRetry;
  }
  function clearGraphs() {
    data = null;
    graphs.replaceChildren();
    csv.disabled = png.disabled = true;
  }
  function reset(workspaceId = '') {
    cancel(); release();
    guard.invalidate(); seriesGuard.invalidate();
    workspace = workspaceId; run = ''; metadata = null; selected = []; cache.clear(); note = '';
    list.replaceChildren(); controls.hidden = true; search.value = ''; search.disabled = true;
    runs.replaceChildren(new Option('NO RUNS', '')); runs.disabled = true;
    clearGraphs(); status('Select a completed run to inspect its results.');
  }
  function setRuns(source, locked = false) {
    runs.replaceChildren(...[...source.options].map(o => new Option(o.textContent, o.value)));
    runs.value = source.value; runs.disabled = locked || source.disabled;
  }
  function renderChannels() {
    list.replaceChildren();
    const query = search.value.toLowerCase();
    for (const channel of metadata.channels.filter(c => `${c.name} ${c.unit}`.toLowerCase().includes(query))) {
      const label = document.createElement('label'); label.className = 'result-channel';
      const input = document.createElement('input'); input.type = 'checkbox'; input.checked = selected.includes(channel.name);
      input.onchange = () => {
        selected = input.checked ? [...selected, channel.name] : selected.filter(n => n !== channel.name);
        loadSeries();
      };
      const name = document.createElement('span'); name.textContent = channel.name;
      const unit = document.createElement('small'); unit.textContent = channel.unit;
      label.append(input, name, unit); list.append(label);
    }
    if (!list.children.length) list.textContent = 'No matching channels.';
  }
  search.oninput = renderChannels;

  async function load(workspaceId, runId, playbackLoad = null) {
    if (workspace !== workspaceId) reset(workspaceId);
    cancel();
    if (run !== runId) { release(); cache.clear(); }
    const controller = pending = new AbortController();
    const token = guard.invalidate(); seriesGuard.invalidate();
    run = runId; runs.value = runId; metadata = null; controls.hidden = true;
    list.replaceChildren(); search.disabled = true; clearGraphs();
    if (!runId) { status('Select a completed run to inspect its results.'); return; }
    status('Loading output channels…');
    const url = `/api/workspaces/${encodeURIComponent(workspaceId)}/runs/${encodeURIComponent(runId)}/results`;
    try {
      const result = await request(url, { signal: controller.signal });
      if (!guard.current(token)) return;
      if (!result.available) { status(result.reason, true); return; }
      metadata = result;
      const available = new Set(result.channels.map(c => c.name));
      const missing = selected.filter(n => !available.has(n));
      selected = selected.filter(n => available.has(n));
      if (!selected.length) selected = ['RotSpeed', 'GenPwr', 'Wind1VelX'].filter(n => available.has(n));
      if (!selected.length && result.channels.length) selected = [result.channels[0].name];
      note = missing.length ? `Unavailable selections: ${missing.join(', ')}. ` : '';
      start = result.start; end = result.end;
      for (const input of [startInput, endInput]) { input.min = start; input.max = end; }
      controls.hidden = false; search.disabled = false; renderChannels(); syncRange();
      if (playbackLoad) {
        const playback = await playbackLoad;
        if (!guard.current(token)) return;
        if (playback?.available) cache.put(playback);
      }
      await loadSeries();
    } catch (error) { if (guard.current(token)) status(`Results unavailable: ${error.message}`, true); }
  }
  retry.onclick = () => metadata ? loadSeries() : load(workspace, run);

  async function loadSeries() {
    const token = seriesGuard.invalidate();
    cancel();
    clearGraphs();
    if (!selected.length) { status('Select at least one output channel.'); return; }
    const controller = pending = new AbortController();
    const names = [...selected];
    const base = `/api/workspaces/${encodeURIComponent(workspace)}/runs/${encodeURIComponent(run)}/results/series`;
    const missing = cache.missing(names);
    const existing = cache.select(names);
    status('Loading time series…');
    try {
      let fetched = null;
      if (missing.length) {
        const key = `${base}?${new URLSearchParams(missing.map(n => ['channel', n]))}`;
        fetched = await request(key, { signal: controller.signal });
      }
      if (!seriesGuard.current(token)) return;
      if (fetched) cache.put(fetched);
      const channels = { ...existing.channels, ...fetched?.channels };
      data = { source: metadata.source, timestamps: existing.timestamps || fetched.timestamps,
        channels: Object.fromEntries(names.map(name => [name, channels[name]])) };
      render();
    } catch (error) { if (seriesGuard.current(token)) status(`Cannot load time series: ${error.message}`, true); }
  }

  function syncRange() { startInput.value = start; endInput.value = end; }
  function range(a, b) {
    if (!metadata || !Number.isFinite(a) || !Number.isFinite(b) || a >= b || a < metadata.start || b > metadata.end) {
      status('Choose start < end within the run time bounds.'); syncRange(); return;
    }
    start = a; end = b; syncRange(); render();
  }
  panel.querySelector('#resultsApply').onclick = () => {
    if (!startInput.value.trim() || !endInput.value.trim()) { status('Enter both start and end times.'); return; }
    range(Number(startInput.value), Number(endInput.value));
  };
  panel.querySelector('#resultsReset').onclick = () => range(metadata.start, metadata.end);
  function render() {
    if (!data || !metadata) return;
    graphs.replaceChildren();
    const displayed = intervalSeries(data, start, end);
    const count = displayed.timestamps.length;
    status(`${note}${metadata.source} · ${count.toLocaleString()} samples · ${format(start)}–${format(end)} s`);
    csv.disabled = png.disabled = count === 0;
    if (!count) { graphs.textContent = 'No samples in this interval. Reset or widen the time range.'; return; }
    for (const [name, channel] of Object.entries(displayed.channels)) {
      const card = document.createElement('section'); card.className = 'result-graph';
      const title = document.createElement('strong'); title.textContent = `${name} (${channel.unit})`;
      const readout = document.createElement('output'); readout.className = 'graph-readout'; readout.textContent = 'Hover for values · drag to zoom';
      const svg = chart(name, channel, displayed.timestamps, readout);
      card.append(title, readout, svg); graphs.append(card);
    }
  }
  function chart(name, channel, times, readout) {
    const width = 440, height = 190, left = 66, right = 425, top = 18, bottom = 150;
    let low = Infinity, high = -Infinity;
    for (const v of channel.values) if (v !== null) { low = Math.min(low, v); high = Math.max(high, v); }
    if (!Number.isFinite(low)) { low = -1; high = 1; }
    if (low === high) { const pad = Math.abs(low) * .05 || 1; low -= pad; high += pad; }
    const x = t => left + (t - start) / (end - start) * (right - left);
    const y = v => bottom - (v - low) / (high - low) * (bottom - top);
    const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${name} in ${channel.unit} versus time in seconds`, tabindex: '0' });
    svg.append(svgNode('rect', { width, height, fill: 'var(--panel)' }));
    for (let i = 0; i <= 4; i++) {
      const yy = top + i / 4 * (bottom - top), t = start + i / 4 * (end - start);
      svg.append(svgNode('line', { x1: left, x2: right, y1: yy, y2: yy, stroke: 'var(--border)' }),
        svgNode('text', { x: left - 6, y: yy + 3, 'text-anchor': 'end', fill: 'var(--text-dim)', 'font-size': 10 }, format(high - i / 4 * (high - low))),
        svgNode('text', { x: x(t), y: bottom + 17, 'text-anchor': 'middle', fill: 'var(--text-dim)', 'font-size': 10 }, format(t)));
    }
    svg.append(svgNode('text', { x: (left + right) / 2, y: height - 5, 'text-anchor': 'middle', fill: 'var(--text-dim)', 'font-size': 11 }, 'Time (s)'));
    let path = '', pen = false;
    for (const [t, v] of reducePoints(times, channel.values, start, end, right - left)) {
      if (v === null) { pen = false; continue; }
      path += `${pen ? 'L' : 'M'}${x(t).toFixed(2)},${y(v).toFixed(2)} `; pen = true;
    }
    svg.append(svgNode('path', { d: path, fill: 'none', stroke: 'var(--blue)', 'stroke-width': 1.5 }));
    if (times.length === 1 && channel.values[0] !== null) svg.append(svgNode('circle', { cx: x(times[0]), cy: y(channel.values[0]), r: 3, fill: 'var(--blue)' }));
    const cursor = svgNode('line', { y1: top, y2: bottom, stroke: 'var(--text-dim)', 'stroke-dasharray': '3 3', visibility: 'hidden' });
    const selection = svgNode('rect', { y: top, height: bottom - top, fill: 'var(--blue)', opacity: .15, visibility: 'hidden' });
    svg.append(cursor, selection);
    const eventTime = e => {
      const rect = svg.getBoundingClientRect();
      return start + Math.max(0, Math.min(1, ((e.clientX - rect.left) / rect.width * width - left) / (right - left))) * (end - start);
    };
    let drag = null;
    svg.onpointermove = e => {
      const t = eventTime(e);
      let lo = 0, hi = times.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (times[mid] < t) lo = mid + 1; else hi = mid; }
      if (lo && t - times[lo - 1] < times[lo] - t) lo--;
      cursor.setAttribute('x1', x(times[lo])); cursor.setAttribute('x2', x(times[lo])); cursor.setAttribute('visibility', 'visible');
      readout.textContent = `${format(times[lo])} s · ${channel.values[lo] === null ? 'Unavailable' : `${format(channel.values[lo])} ${channel.unit}`}`;
      if (drag !== null) { selection.setAttribute('x', x(Math.min(drag, t))); selection.setAttribute('width', Math.abs(x(t) - x(drag))); selection.setAttribute('visibility', 'visible'); }
    };
    svg.onpointerleave = () => { cursor.setAttribute('visibility', 'hidden'); };
    svg.onpointerdown = e => { if (e.button !== 0) return; drag = eventTime(e); svg.setPointerCapture(e.pointerId); };
    svg.onpointerup = e => { if (drag === null) return; const t = eventTime(e), a = drag; drag = null; selection.setAttribute('visibility', 'hidden'); if (Math.abs(x(t) - x(a)) > 5) range(Math.min(a, t), Math.max(a, t)); };
    svg.onpointercancel = () => { drag = null; selection.setAttribute('visibility', 'hidden'); };
    svg.onkeydown = e => {
      if (e.key === '+' || e.key === '=') { e.preventDefault(); const pad = (end - start) / 4; range(start + pad, end - pad); }
      if (e.key === '0') { e.preventDefault(); range(metadata.start, metadata.end); }
    };
    return svg;
  }
  function download(blob, suffix, filename = `${run}-${start}-${end}`) {
    const url = URL.createObjectURL(blob), link = document.createElement('a');
    link.href = url; link.download = `${filename}.${suffix}`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  csv.onclick = () => download(new Blob([seriesCSV(intervalSeries(data, start, end))], { type: 'text/csv;charset=utf-8' }), 'csv');
  png.onclick = async () => {
    png.disabled = true;
    const exportRun = run, exportStart = start, exportEnd = end, exportSource = metadata.source;
    const exportToken = seriesGuard.invalidate();
    const cards = [...graphs.querySelectorAll('.result-graph')].map(card => ({
      title: card.querySelector('strong').textContent, svg: card.querySelector('svg').cloneNode(true),
    }));
    try {
      const canvas = document.createElement('canvas'); canvas.width = 960; canvas.height = 100 + cards.length * 450;
      const ctx = canvas.getContext('2d');
      const style = getComputedStyle(panel);
      ctx.fillStyle = style.getPropertyValue('--panel').trim(); ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = style.getPropertyValue('--text').trim(); ctx.font = '18px sans-serif';
      ctx.fillText(exportRun, 24, 30); ctx.font = '14px sans-serif';
      ctx.fillText(`${exportSource} · ${exportStart}–${exportEnd} s`, 24, 60);
      for (let i = 0; i < cards.length; i++) {
        const clone = cards[i].svg;
        for (const node of clone.querySelectorAll('*')) {
          for (const attr of ['fill', 'stroke']) {
            const value = node.getAttribute(attr);
            if (value?.startsWith('var(')) node.setAttribute(attr, style.getPropertyValue(value.slice(4, -1)).trim());
          }
        }
        // Omit interactive overlays from the exported graph.
        clone.querySelectorAll('[visibility]').forEach(node => node.remove());
        clone.setAttribute('xmlns', NS); clone.setAttribute('width', '880'); clone.setAttribute('height', '380');
        const url = URL.createObjectURL(new Blob([new XMLSerializer().serializeToString(clone)], { type: 'image/svg+xml' }));
        try {
          const img = new Image(); img.src = url; await img.decode();
          ctx.fillText(cards[i].title, 24, 100 + i * 450);
          ctx.drawImage(img, 24, 115 + i * 450, 880, 380);
        } finally { URL.revokeObjectURL(url); }
      }
      const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
      if (!blob) throw new Error('Image export failed.');
      download(blob, 'png', `${exportRun}-${exportStart}-${exportEnd}`);
    } catch (error) { if (seriesGuard.current(exportToken)) status(`PNG export failed: ${error.message}`); }
    finally { if (seriesGuard.current(exportToken)) png.disabled = !data || !intervalSeries(data, start, end).timestamps.length; }
  };
  reset();
  return { reset, load, setRuns };
}
