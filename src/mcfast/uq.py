"""Validated, reproducible sampling with independent marginal distributions."""
import math
import random
from statistics import NormalDist
from scipy.stats import qmc

SAMPLING_METHODS = {'monte_carlo', 'lhs', 'sobol', 'halton'}


def validate_uq(variables, config):
    method = config.get('method')
    if not isinstance(method, str) or method not in SAMPLING_METHODS:
        raise ValueError('Choose Monte Carlo, Latin hypercube, Sobol, or Halton sampling')
    count = config.get('count')
    seed = config.get('seed')
    if type(count) is not int or not 1 <= count <= 100000 or count * len(variables) > 1000000:
        raise ValueError('Use 1–100,000 cases and at most 1,000,000 values')
    if method == 'sobol' and count & (count - 1):
        raise ValueError('Sobol requires a power-of-two case count (e.g. 64, 128, 256; maximum 65,536)')
    if type(seed) is not int or not 0 <= seed <= 4294967295:
        raise ValueError('Seed must be an integer from 0 to 4,294,967,295')
    specs = config.get('variables', {})
    if not isinstance(specs, dict) or set(specs) != {v['name'] for v in variables}:
        raise ValueError('Configure every study variable')
    cleaned = {}
    for variable in variables:
        name, kind = variable['name'], variable['kind']
        spec = specs[name]
        if not isinstance(spec, dict):
            raise ValueError(f'{name}: invalid distribution settings')
        distribution = spec.get('distribution')
        if kind not in {'number', 'integer'}:
            if distribution != 'fixed':
                raise ValueError(f'{name}: nonnumeric variables must use the model value')
            cleaned[name] = {'distribution': 'fixed'}
            continue
        if distribution not in ({'uniform'} if kind == 'integer' else {'uniform', 'normal'}):
            raise ValueError(f'{name}: unsupported distribution')
        def number(key):
            value = spec.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name}: {key} must be a finite number')
            return value
        low, high = number('minimum'), number('maximum')
        if low >= high or not math.isfinite(high - low):
            raise ValueError(f'{name}: minimum must be less than maximum')
        if kind == 'integer' and (not float(low).is_integer() or not float(high).is_integer()):
            raise ValueError(f'{name}: integer variables require integer bounds')
        item = {'distribution': distribution, 'minimum': low, 'maximum': high}
        if distribution == 'normal':
            mean, sd = number('mean'), number('stddev')
            if sd <= 0:
                raise ValueError(f'{name}: standard deviation must be positive')
            normal = NormalDist(mean, sd)
            a, b = normal.cdf(low), normal.cdf(high)
            if b - a < 1e-12:
                raise ValueError(f'{name}: bounds contain too little normal probability; adjust mean or deviation')
            item.update(mean=mean, stddev=sd)
        cleaned[name] = item
    return {'method': method, 'count': count, 'seed': seed, 'variables': cleaned}


def sample_uq(variables, config):
    config = validate_uq(variables, config)
    rng = random.Random(config['seed'])
    numeric = [v for v in variables if v['kind'] in {'number', 'integer'}]
    design = None
    if config['method'] != 'monte_carlo' and numeric:
        engine_type = {'lhs': qmc.LatinHypercube, 'sobol': qmc.Sobol, 'halton': qmc.Halton}[config['method']]
        engine = engine_type(d=len(numeric), scramble=True, rng=config['seed'])
        design = (engine.random_base2(config['count'].bit_length() - 1)
                  if config['method'] == 'sobol' else engine.random(config['count']))
    dimension = 0
    columns = {}
    for variable in variables:
        spec = config['variables'][variable['name']]
        distribution = spec['distribution']
        if distribution == 'fixed':
            values = [variable['original_value']] * config['count']
        else:
            low, high = spec['minimum'], spec['maximum']
            quantiles = design[:, dimension] if design is not None else None
            dimension += 1
            if distribution == 'normal':
                normal = NormalDist(spec['mean'], spec['stddev'])
                a, b = normal.cdf(low), normal.cdf(high)
                quantiles = quantiles if quantiles is not None else [rng.random() for _ in range(config['count'])]
                values = [max(low, min(high, normal.inv_cdf(max(1e-16, min(1 - 1e-16, a + (b - a) * float(u)))))) for u in quantiles]
            elif variable['kind'] == 'integer':
                values = ([min(int(high), int(low) + int(float(u) * (int(high) - int(low) + 1))) for u in quantiles]
                          if quantiles is not None else [rng.randint(int(low), int(high)) for _ in range(config['count'])])
            else:
                values = ([max(low, min(high, low + (high - low) * float(u))) for u in quantiles]
                          if quantiles is not None else [rng.uniform(low, high) for _ in range(config['count'])])
        columns[variable['name']] = values
    return config, [{name: values[i] for name, values in columns.items()} for i in range(config['count'])]
