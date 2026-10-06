import { samplingHint, samplingCountError } from './uq-sampling.js';
export function createUqPanel({ request, notify, hasUnsavedStudy, onSaved }) {
  const panel = document.querySelector('#uqPanel');
  panel.innerHTML = `<div class="uq-content">
    <span class="modal-eyebrow">UNCERTAINTY QUANTIFICATION</span><h2>UQ Method</h2>
    <p class="tree-hint">Load a variable study, define independent distributions, then generate study cases.</p>
    <label class="field"><span>SAVED STUDY</span><select id="uqStudy"><option value="">Choose a study</option></select></label>
    <p id="uqMessage" class="tree-hint" role="status">Save variables in Variable Study to get started. Cases can be added later.</p>
    <form id="uqForm" hidden>
      <section class="modal-section"><h3>Variable distributions</h3><p class="tree-hint">Numeric bounds are required. Normal distributions are truncated to these bounds. Integer variables use discrete uniform sampling; other variables keep their model value.</p><div id="uqVariables" class="variable-list"></div></section>
      <section class="modal-section"><h3>Sampling</h3><div class="uq-grid">
        <label class="field"><span>METHOD</span><select name="method"><option value="monte_carlo">Monte Carlo</option><option value="lhs">Latin hypercube (LHS)</option><option value="sobol">Sobol (scrambled)</option><option value="halton">Halton (scrambled)</option></select></label>
        <label class="field"><span>NUMBER OF CASES / RUNS</span><input name="count" type="number" min="1" max="100000" step="1" value="100" required></label>
        <label class="field"><span>RANDOM SEED</span><input name="seed" type="number" min="0" max="4294967295" step="1" value="42" required></label>
      </div><p id="uqMethodHint" class="tree-hint"></p><p class="tree-hint">One generated case corresponds to one simulation run. Maximum 100,000 cases and 1,000,000 values.</p></section>
      <div class="variable-actions"><button class="secondary-btn" id="uqGenerate" type="submit">GENERATE PREVIEW</button></div>
      <div id="uqPreview" hidden></div>
      <p class="tree-hint">Saving replaces the selected study’s case table. Launch the saved cases from Simulation.</p>
      <button class="create-workspace-btn" id="uqSave" type="button" disabled>SAVE SAMPLES TO STUDY</button>
    </form></div>`;
  const select = panel.querySelector('#uqStudy');
  const form = panel.querySelector('#uqForm');
  const message = panel.querySelector('#uqMessage');
  const list = panel.querySelector('#uqVariables');
  const preview = panel.querySelector('#uqPreview');
  const save = panel.querySelector('#uqSave');
  const generate = panel.querySelector('#uqGenerate');
  let workspaceId = null, study = null, generated = null, revision = 0;
  const url = suffix => `/api/workspaces/${encodeURIComponent(workspaceId)}${suffix}`;
  function invalidate() { revision++; generated = null; save.disabled = true; generate.disabled = false; preview.hidden = true; }
  function setWorkspace(id) {
    workspaceId = id; study = null; invalidate(); form.hidden = true;
    select.innerHTML = '<option value="">Choose a study</option>';
    message.textContent = 'Save variables in Variable Study to get started. Cases can be added later.';
  }
  async function refresh() {
    if (!workspaceId) return;
    const id = workspaceId, token = revision, chosen = select.value;
    const result = await request(url('/studies'));
    if (id !== workspaceId || token !== revision) return;
    select.innerHTML = '<option value="">Choose a study</option>';
    result.studies.forEach(item => select.add(new Option(`${item.name} · ${item.sample_count} cases`, item.study_id)));
    select.value = chosen;
  }
  const field = (label, name, attrs = '') => `<label class="field"><span>${label}</span><input data-setting="${name}" type="number" step="any" ${attrs} required></label>`;
  select.onchange = async () => {
    invalidate(); study = null; form.hidden = true;
    if (!select.value || !workspaceId) return;
    const token = revision;
    try {
      const result = await request(url(`/studies/${encodeURIComponent(select.value)}`));
      if (token !== revision) return;
      study = result; list.replaceChildren();
      for (const variable of study.variables) {
        const row = document.createElement('div'); row.className = 'uq-variable';
        const heading = document.createElement('strong'); heading.textContent = variable.name;
        const binding = document.createElement('small'); binding.textContent = `${variable.file} · ${variable.key} · model value: ${variable.original_value}`;
        row.append(heading, binding);
        const spec = study.uq?.variables?.[variable.name] || {};
        if (['number', 'integer'].includes(variable.kind)) {
          const controls = document.createElement('div'); controls.className = 'uq-grid';
          controls.innerHTML = `<label class="field"><span>DISTRIBUTION</span><select data-setting="distribution"><option value="uniform">${variable.kind === 'integer' ? 'Discrete uniform' : 'Uniform'}</option>${variable.kind === 'number' ? '<option value="normal">Truncated normal</option>' : ''}</select></label>${field('MINIMUM', 'minimum')}${field('MAXIMUM', 'maximum')}<div class="uq-normal uq-grid" hidden>${field('MEAN', 'mean')}${field('STANDARD DEVIATION', 'stddev', 'min="0"')}</div>`;
          row.append(controls);
          for (const key of ['distribution', 'minimum', 'maximum', 'mean', 'stddev']) {
            if (spec[key] !== undefined) row.querySelector(`[data-setting="${key}"]`).value = spec[key];
          }
          if (variable.kind === 'integer') for (const key of ['minimum', 'maximum']) row.querySelector(`[data-setting="${key}"]`).step = '1';
          const distribution = row.querySelector('select');
          const toggle = () => {
            const normal = distribution.value === 'normal';
            row.querySelector('.uq-normal').hidden = !normal;
            row.querySelectorAll('.uq-normal input').forEach(input => { input.disabled = !normal; });
          };
          distribution.onchange = toggle; toggle();
        } else {
          const fixed = document.createElement('p'); fixed.className = 'tree-hint'; fixed.textContent = 'Fixed at model value'; row.append(fixed);
        }
        list.append(row);
      }
      form.elements.method.value = study.uq?.method ?? 'monte_carlo';
      form.elements.count.value = study.uq?.count ?? 100;
      updateSamplingHint();
      form.elements.seed.value = study.uq?.seed ?? 42;
      message.textContent = `${study.name} · ${study.samples.length} saved cases`;
      generate.disabled = false; form.hidden = false;
    } catch (error) { if (token === revision) { message.textContent = error.message; notify(error.message); } }
  };
  function updateSamplingHint() {
    const method = form.elements.method.value;
    const count = Number(form.elements.count.value);
    panel.querySelector('#uqMethodHint').textContent = samplingHint(method);
    form.elements.count.setCustomValidity(samplingCountError(method, count));
  }
  form.elements.method.addEventListener('change', updateSamplingHint);
  form.elements.count.addEventListener('input', updateSamplingHint);
  form.addEventListener('input', invalidate);
  form.addEventListener('change', invalidate);
  form.onsubmit = async event => {
    event.preventDefault(); if (!study) return;
    invalidate(); const token = revision;
    const variables = Object.fromEntries(study.variables.map((variable, i) => {
      const row = list.children[i];
      const spec = ['number', 'integer'].includes(variable.kind)
        ? Object.fromEntries([...row.querySelectorAll('[data-setting]')].filter(input => !input.disabled).map(input => [input.dataset.setting, input.tagName === 'SELECT' ? input.value : Number(input.value)]))
        : { distribution: 'fixed' };
      return [variable.name, spec];
    }));
    generate.disabled = true; message.textContent = 'Generating samples…';
    try {
      const result = await request(url(`/studies/${encodeURIComponent(study.study_id)}/sample`), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ uq: { method: form.elements.method.value, count: Number(form.elements.count.value), seed: Number(form.elements.seed.value), variables } }) });
      if (token !== revision) return;
      generated = result; save.disabled = false; preview.hidden = false; preview.replaceChildren();
      const title = document.createElement('p'); title.className = 'tree-hint'; title.textContent = `${result.samples.length} cases ready · first ${Math.min(5, result.samples.length)} shown`;
      const pre = document.createElement('pre'); pre.textContent = JSON.stringify(result.samples.slice(0, 5), null, 2); preview.append(title, pre);
      message.textContent = 'Preview ready. Save to replace the study’s cases.';
    } catch (error) { if (token === revision) { message.textContent = error.message; notify(error.message); } }
    finally { if (token === revision) generate.disabled = false; }
  };
  save.onclick = async () => {
    if (!study || !generated) return;
    if (hasUnsavedStudy()) return notify('Save your Variable Study edits before updating its samples');
    const token = revision, id = study.study_id;
    save.disabled = true; generate.disabled = true; form.inert = true;
    try {
      await request(url(`/studies/${encodeURIComponent(id)}`), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: study.name, variables: study.variables, samples: generated.samples, uq: generated.uq, expected_updated_at: study.updated_at }) });
      if (token !== revision) return;
      study.samples = generated.samples; study.uq = generated.uq;
      message.textContent = `${study.samples.length} cases saved. Open Simulation to launch the MC runs.`;
      generated = null;
      study = await request(url(`/studies/${encodeURIComponent(id)}`));
      if (token !== revision) return;
      await onSaved(id); await refresh(); notify('Sampled cases saved to study');
    } catch (error) { if (token === revision) { message.textContent = error.message; save.disabled = false; } }
    finally { form.inert = false; if (token === revision) generate.disabled = false; }
  };
  return { setWorkspace, refresh };
}
