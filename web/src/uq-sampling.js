export function samplingHint(method) {
  return {
    monte_carlo: 'Independent random draws. The seed makes the samples reproducible.',
    lhs: 'Latin hypercube places one sample in each equal-probability interval of every numeric variable.',
    sobol: 'Scrambled Sobol spreads samples across the parameter space. Use a power-of-two case count: 64, 128, 256, … (maximum 65,536). This generates a Sobol sequence for sampling.',
    halton: 'Scrambled Halton spreads samples across the parameter space and supports any case count.',
  }[method] || '';
}

export function samplingCountError(method, count) {
  if (method === 'sobol' && (!Number.isInteger(count) || count < 1 || count > 65536 || (count & (count - 1)) !== 0)) {
    return 'Sobol requires a power-of-two case count between 1 and 65,536 (e.g. 64, 128, 256).';
  }
  return '';
}
