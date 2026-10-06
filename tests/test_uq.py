import pytest
from mcfast.uq import sample_uq, validate_uq

VARIABLES = [
    {'name': 'wind', 'kind': 'number', 'original_value': 10},
    {'name': 'blades', 'kind': 'integer', 'original_value': 3},
    {'name': 'enabled', 'kind': 'boolean', 'original_value': True},
]


def config(distribution='uniform'):
    return {'method': 'monte_carlo', 'count': 2000, 'seed': 42, 'variables': {
        'wind': {'distribution': distribution, 'minimum': 5, 'maximum': 15, 'mean': 10, 'stddev': 1},
        'blades': {'distribution': 'uniform', 'minimum': 2, 'maximum': 4},
        'enabled': {'distribution': 'fixed'},
    }}


@pytest.mark.parametrize('method', ['monte_carlo', 'lhs', 'sobol', 'halton'])
@pytest.mark.parametrize('distribution', ['uniform', 'normal'])
def test_reproducible_bounded_typed_samples(distribution, method):
    setup = config(distribution); setup.update(method=method, count=2048)
    settings, samples = sample_uq(VARIABLES, setup)
    assert samples == sample_uq(VARIABLES, settings)[1]
    assert all(5 <= row['wind'] <= 15 and type(row['blades']) is int and row['enabled'] is True for row in samples)
    assert {row['blades'] for row in samples} == {2, 3, 4}
    mean = sum(row['wind'] for row in samples) / len(samples)
    variance = sum((row['wind'] - mean) ** 2 for row in samples) / len(samples)
    assert abs(mean - 10) < .2
    assert (7 < variance < 9.5) if distribution == 'uniform' else (.85 < variance < 1.15)


@pytest.mark.parametrize('key,value', [('minimum', None), ('minimum', 15), ('maximum', float('inf')), ('stddev', 0), ('mean', 100)])
def test_invalid_normal_bounds_and_parameters(key, value):
    settings = config('normal')
    settings['variables']['wind'][key] = value
    with pytest.raises(ValueError):
        validate_uq(VARIABLES, settings)


@pytest.mark.parametrize('key,value', [('count', 0), ('count', True), ('count', 100001), ('seed', -1), ('seed', 1.5)])
def test_invalid_run_settings(key, value):
    settings = config(); settings[key] = value
    with pytest.raises(ValueError):
        validate_uq(VARIABLES, settings)


def test_invalid_integer_bounds_and_missing_variable():
    settings = config(); settings['variables']['blades']['minimum'] = 1.5
    with pytest.raises(ValueError):
        validate_uq(VARIABLES, settings)
    del settings['variables']['enabled']
    with pytest.raises(ValueError):
        validate_uq(VARIABLES, settings)


@pytest.mark.parametrize('method', ['lhs', 'sobol'])
def test_probability_strata_are_covered_in_each_dimension(method):
    variables = [{'name': name, 'kind': 'number', 'original_value': 0} for name in ['x', 'y']]
    settings = {'method': method, 'count': 64, 'seed': 42, 'variables': {
        name: {'distribution': 'uniform', 'minimum': 0, 'maximum': 1} for name in ['x', 'y']
    }}
    _, samples = sample_uq(variables, settings)
    for name in ['x', 'y']:
        assert sorted(int(sample[name] * 64) for sample in samples) == list(range(64))
    settings['seed'] = 43
    assert samples != sample_uq(variables, settings)[1]


@pytest.mark.parametrize('method', ['lhs', 'sobol', 'halton'])
def test_fixed_variables_do_not_consume_sampling_dimensions(method):
    setup = config(); setup.update(method=method, count=32)
    _, samples = sample_uq(VARIABLES, setup)
    del setup['variables']['enabled']
    assert [{k: v for k, v in sample.items() if k != 'enabled'} for sample in samples] == sample_uq(VARIABLES[:2], setup)[1]
    fixed_config = {'method': method, 'count': 32, 'seed': 42, 'variables': {'enabled': {'distribution': 'fixed'}}}
    assert sample_uq(VARIABLES[2:], fixed_config)[1] == [{'enabled': True}] * 32


@pytest.mark.parametrize('count', [3, 100, 100000])
def test_sobol_rejects_non_power_of_two_count(count):
    setup = config(); setup.update(method='sobol', count=count)
    with pytest.raises(ValueError, match='power-of-two'):
        sample_uq(VARIABLES, setup)


@pytest.mark.parametrize('method', ['unknown', None, [], {}])
def test_unsupported_sampling_method(method):
    setup = config(); setup['method'] = method
    with pytest.raises(ValueError, match='Choose'):
        validate_uq(VARIABLES, setup)
