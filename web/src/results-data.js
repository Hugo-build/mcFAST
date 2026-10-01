export function intervalSeries(data, start, end) {
  const indices = data.timestamps.flatMap((t, i) => t >= start && t <= end ? [i] : []);
  return { ...data, timestamps: indices.map(i => data.timestamps[i]), channels: Object.fromEntries(
    Object.entries(data.channels).map(([name, c]) => [name, { unit: c.unit, values: indices.map(i => c.values[i]) }])) };
}

export function seriesCSV(data) {
  const quote = value => /[",\r\n]/.test(String(value)) ? `"${String(value).replaceAll('"', '""')}"` : String(value);
  const names = Object.keys(data.channels);
  return [[ 'Time (s)', ...names.map(n => `${n} (${data.channels[n].unit})`) ].map(quote).join(','),
    ...data.timestamps.map((t, i) => [t, ...names.map(n => data.channels[n].values[i] ?? '')].map(quote).join(','))].join('\r\n') + '\r\n';
}

// Retain extrema and gap boundaries in each horizontal pixel bucket.
export function reducePoints(times, values, start, end, width) {
  const buckets = new Map();
  const keep = new Set();
  for (let i = 0; i < times.length; i++) {
    if (values[i] === null) {
      if (i === 0 || values[i - 1] !== null) { keep.add(i); if (i) keep.add(i - 1); }
      if (i + 1 < times.length && values[i + 1] !== null) keep.add(i + 1);
      continue;
    }
    const pixel = Math.floor((times[i] - start) / (end - start || 1) * width);
    const b = buckets.get(pixel);
    if (!b) buckets.set(pixel, { first: i, last: i, min: i, max: i });
    else { b.last = i; if (values[i] < values[b.min]) b.min = i; if (values[i] > values[b.max]) b.max = i; }
  }
  for (const b of buckets.values()) for (const i of [b.first, b.last, b.min, b.max]) keep.add(i);
  return [...keep].sort((a, b) => a - b).map(i => [times[i], values[i]]);
}

export function requestGuard() {
  let generation = 0;
  return { invalidate: () => ++generation, current: token => token === generation };
}

// Store channels once per run, rather than duplicating overlapping selections.
// The allowance is conservative for numeric JavaScript arrays and null entries.
export function channelCache(budget = 32 * 1024 * 1024) {
  const channels = new Map();
  let timestamps = null, bytes = 0;
  return {
    clear() { channels.clear(); timestamps = null; bytes = 0; },
    missing(names) { return names.filter(name => !channels.has(name)); },
    put(series) {
      timestamps ??= series.timestamps;
      for (const [name, channel] of Object.entries(series.channels)) {
        const size = channel.values.length * 16;
        if (channels.has(name)) { bytes -= channels.get(name).size; channels.delete(name); }
        while (channels.size && bytes + size > budget) {
          const oldest = channels.keys().next().value;
          bytes -= channels.get(oldest).size; channels.delete(oldest);
        }
        if (size <= budget) { channels.set(name, { channel, size }); bytes += size; }
      }
    },
    select(names) {
      const selected = {};
      for (const name of names) if (channels.has(name)) {
        const item = channels.get(name);
        channels.delete(name); channels.set(name, item);
        selected[name] = item.channel;
      }
      return { timestamps, channels: selected };
    },
  };
}
