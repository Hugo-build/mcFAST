from pathlib import Path

import pytest

from mcfast.parser import parameter_map, parse_text, update_file


FIXTURE = Path(__file__).parent / "fixtures" / "minimal.fst"


def test_parses_typed_scalar_values_and_references():
    parameters = parse_text(FIXTURE.read_text())
    data = parameter_map(parameters)
    assert data["EDFile"] == "IEA-15-240-RWT_ElastoDyn.dat"
    assert data["Echo"] is True
    assert data["TMax"] == 600.0
    assert next(p for p in parameters if p.key == "EDFile").reference.endswith(".dat")
    assert "RotSpeed" not in data


def test_update_is_lossless_outside_the_value(tmp_path):
    target = tmp_path / "minimal.fst"
    original = FIXTURE.read_text()
    target.write_text(original)
    result = update_file(target, {"TMax": 42.5, "Echo": False})
    updated = target.read_text()
    assert "42.5                              TMax     - Total run time (s)" in updated
    assert "False                               Echo" in updated
    assert updated.split("OutList", 1)[1] == original.split("OutList", 1)[1]
    assert result["data"]["TMax"] == 42.5


def test_optimistic_write_conflict(tmp_path):
    target = tmp_path / "minimal.fst"
    target.write_text(FIXTURE.read_text())
    with pytest.raises(RuntimeError, match="changed on disk"):
        update_file(target, {"TMax": 1}, expected_mtime_ns=1)

