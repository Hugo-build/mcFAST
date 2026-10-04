export const terminal = status => ['completed', 'failed', 'cancelled', 'interrupted'].includes(status);
export function workerSlots(targets, values) {
  const slots = {};
  for (const target of targets) {
    const count = Number(values[target.target_id] || 0);
    if (!Number.isInteger(count) || count < 0) throw new Error('Worker slots must be non-negative integers');
    if (count && !target.ready) throw new Error(`${target.name}: OpenFAST is unavailable`);
    const limit = target.cpu_capacity;
    if (count > limit) throw new Error(`${target.name}: select at most ${limit} slots`);
    if (count) slots[target.target_id] = count;
  }
  if (!Object.keys(slots).length) throw new Error('Select at least one worker');
  return slots;
}

export function createSimulationPanel({ request, hasUnsavedStudy, onResult, onProgress, notify }) {
  const panel = document.querySelector('#simulationPanel');
  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const button = (text, action) => {
    const element = node('button', text, 'secondary-btn');
    element.type = 'button';
    element.onclick = async () => {
      element.disabled = true;
      try { await action(); } catch (error) { notify(error.message); }
      finally { element.disabled = false; renderControls(); }
    };
    return element;
  };
  const study = node('select'); study.setAttribute('aria-label', 'Simulation saved study');
  const batchSelect = node('select'); batchSelect.setAttribute('aria-label', 'Simulation batch history');
  const sampleCount = node('p');
  const targetsBox = node('div');
  const status = node('p');
  status.setAttribute('role', 'status');
  const table = node('table', undefined, 'case-table simulation-cases');
  const head = node('thead');
  const heading = node('tr');
  ['Sample', 'Phase', 'Status', 'Result'].forEach(text => heading.append(node('th', text)));
  head.append(heading);
  const body = node('tbody'); table.append(head, body);
  const pager = node('div', undefined, 'simulation-actions');
  let workspace = null, generation = 0, targets = [], studies = [], batch = null, page = 1;
  let refreshing = false, inspectedRun = null;
  const values = {};
  function path(suffix) { return `/api/workspaces/${encodeURIComponent(workspace)}/batches${suffix}`; }
  function post(url, payload = {}) { return request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }); }
  function renderControls() {
    const dirty = hasUnsavedStudy();
    run.disabled = !workspace || !study.value || dirty || (batch && !terminal(batch.status));
    run.title = dirty ? 'Save your study edits before launching simulations' : '';
    stop.disabled = !batch || terminal(batch.status) || batch.stop_requested;
    retry.disabled = !batch || !terminal(batch.status) || !(batch.counts.failed || batch.counts.interrupted);
    const selected = studies.find(item => item.study_id === study.value);
    sampleCount.textContent = dirty ? 'Save your study edits before launching simulations.' : selected ? `${selected.sample_count} samples · ${selected.variable_count} ${selected.variable_count === 1 ? 'variable' : 'variables'}` : 'Save a variable study to run its cases.';
  }
  async function loadBatch() {
    if (!batchSelect.value || !workspace) { batch = null; body.replaceChildren(); renderControls(); return; }
    const epoch = generation;
    const selected = batchSelect.value;
    const payload = await request(path(`/${encodeURIComponent(selected)}?page=${page}`));
    if (epoch !== generation || selected !== batchSelect.value) return;
    batch = payload;
    status.textContent = `${payload.status.toUpperCase()} · ${Object.entries(payload.counts).map(([key, n]) => `${n} ${key.replaceAll('_', ' ')}`).join(' · ')}`;
    body.replaceChildren();
    for (const item of payload.cases) {
      const row = node('tr');
      for (const value of [item.sample_index + 1, item.phase, item.status]) row.append(node('td', value));
      row.title = item.error || '';
      const cell = node('td');
      if (item.target) cell.append(button(terminal(item.status) ? 'OPEN' : 'CONSOLE', async () => {
        inspectedRun = terminal(item.status) ? null : item.run_id;
        await onResult(item.run_id, item.status);
      }));
      row.append(cell); body.append(row);
    }
    pager.replaceChildren();
    const previous = button('PREVIOUS', async () => { page -= 1; await loadBatch(); }); previous.disabled = page === 1;
    const next = button('NEXT', async () => { page += 1; await loadBatch(); }); next.disabled = page * payload.page_size >= payload.total;
    pager.append(previous, node('span', `Page ${page} / ${Math.max(1, Math.ceil(payload.total / payload.page_size))}`), next);
    renderControls();
    if (inspectedRun && onProgress && await onProgress(inspectedRun)) inspectedRun = null;
  }
  async function refresh() {
    if (refreshing || !workspace) return;
    refreshing = true;
    const epoch = generation;
    try {
      const [saved, history, available] = await Promise.all([
        request(`/api/workspaces/${encodeURIComponent(workspace)}/studies`), request(path('')), request('/api/simulation/targets'),
      ]);
      if (epoch !== generation) return;
      studies = saved.studies;
      const oldStudy = study.value, oldBatch = batchSelect.value;
      study.replaceChildren(...studies.map(item => new Option(item.name, item.study_id)));
      if (studies.some(item => item.study_id === oldStudy)) study.value = oldStudy;
      batchSelect.replaceChildren(...history.batches.map(item => new Option(`${item.study_name} · ${item.status} · ${item.batch_id}`, item.batch_id)));
      if (history.batches.some(item => item.batch_id === oldBatch)) batchSelect.value = oldBatch;
      targets = available.targets;
      const editingSlots = targetsBox.contains(document.activeElement);
      if (!editingSlots) targetsBox.replaceChildren();
      for (const target of targets) {
        const card = node('div', undefined, 'simulation-target');
        const name = node('strong', target.name);
        const info = `${target.cpu_capacity ?? '?'} usable CPUs · ${target.busy || 0} occupied · ${target.ready ? 'ready' : 'OpenFAST unavailable'}`;
        card.append(name, node('small', info));
        if (target.error) card.append(node('small', target.error, 'simulation-error'));
        const count = node('input'); count.type = 'number'; count.min = '0';
        count.max = (target.cpu_capacity || 1);
        count.value = values[target.target_id] ?? (target.target_id === 'local' ? target.recommended_slots : 0);
        values[target.target_id] = count.value;
        count.disabled = !target.ready; count.setAttribute('aria-label', `${target.name} worker slots`);
        count.oninput = () => { values[target.target_id] = count.value; };
        const label = node('label', 'Worker slots '); label.append(count); card.append(label);
        if (!editingSlots) targetsBox.append(card);
      }
      await loadBatch(); renderControls();
    } finally {
      refreshing = false;
      if (epoch !== generation && workspace) refresh().catch(error => { status.textContent = error.message; });
    }
  }
  const run = button('RUN STUDY', async () => {
    if (hasUnsavedStudy()) throw new Error('Save study edits first');
    const epoch = generation;
    const result = await post(path(''), { study_id: study.value, slots: workerSlots(targets, values) });
    if (epoch !== generation) return;
    batchSelect.add(new Option(result.batch_id, result.batch_id)); batchSelect.value = result.batch_id; page = 1; await refresh();
  });
  run.classList.add('simulation-run');
  const stop = button('STOP NEW CASES', async () => { await post(path(`/${batch.batch_id}/stop`)); await loadBatch(); });
  const retry = button('RETRY FAILED CASES', async () => {
    const epoch = generation;
    const result = await post(path(`/${batch.batch_id}/retry`), { slots: workerSlots(targets, values) });
    if (epoch !== generation) return;
    batchSelect.add(new Option(result.batch_id, result.batch_id)); batchSelect.value = result.batch_id; page = 1; await refresh();
  });
  study.onchange = renderControls;
  batchSelect.onchange = () => { page = 1; loadBatch().catch(error => notify(error.message)); };
  const actions = node('div', undefined, 'simulation-actions'); actions.append(run, stop, retry);
  const setup = node('div', undefined, 'simulation-target-form');
  for (const [key, text, placeholder] of [
    ['host', 'SSH login / host', 'user@host or SSH alias'],
    ['folder', 'Remote folder', '/path/to/project'],
  ]) {
    const label = node('label', text);
    const input = node('input'); input.type = 'text'; input.placeholder = placeholder;
    input.value = localStorage.getItem(`mcfast.ssh.${key}`) || '';
    input.onchange = () => localStorage.setItem(`mcfast.ssh.${key}`, input.value);
    label.append(input); setup.append(label);
  }
  setup.append(node('small', 'Setup fields are saved in this browser. SSH connection and remote execution are planned for a later version.'));
  const details = node('details'); details.append(node('summary', 'SSH setup'), setup);
  const wrapping = node('div', undefined, 'case-table-wrap'); wrapping.append(table);
  panel.append(node('h2', 'Simulation'), node('p', 'One worker runs one sample. Detected capacity does not guarantee idle CPUs.'),
    node('label', 'Saved study'), study, sampleCount, targetsBox, button('REFRESH WORKERS', refresh), details,
    actions, node('label', 'Batch history'), batchSelect, status, wrapping, pager);
  setInterval(() => {
    renderControls();
    if (!panel.hidden && workspace) refresh().catch(error => { status.textContent = error.message; });
  }, 2000);
  renderControls();
  return {
    refresh,
    setWorkspace(id) {
      if (id === workspace) return;
      workspace = id; generation += 1; batch = null; inspectedRun = null; page = 1; study.replaceChildren(); batchSelect.replaceChildren();
      body.replaceChildren(); targetsBox.replaceChildren(); status.textContent = ''; renderControls();
      if (workspace) refresh().catch(error => { status.textContent = error.message; });
    },
  };
}
