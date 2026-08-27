import importlib.util
from pathlib import Path
import sys

import pytest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "generate_turbsim_inputs.py"
SPEC = importlib.util.spec_from_file_location("generate_turbsim_inputs", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
TurbSimSettings = MODULE.TurbSimSettings
generate_cases = MODULE.generate_cases


TEMPLATE = """---------TurbSim input file---------
60362647 RandSeed1 - seed
True WrADFF - write bts
False WrBLFF - write bladed
False WrADTWR - write tower points
True Clockwise - legacy output ordering flag
21 NumGrid_Z - grid
21 NumGrid_Y - grid
0.05 TimeStep - seconds
720.0 AnalysisTime - seconds
\"ALL\" UsableTime - seconds
150.0 HubHt - metres
252.0 GridHeight - metres
252.0 GridWidth - metres
\"IECKAI\" TurbModel - model
\"1-ED3\" IECstandard - standard
B IECturbc - category
1ETM IEC_WindType - wind type
\"PL\" WindProfileType - profile
150.0 RefHt - metres
50.0 URef - m/s
"""


def test_generate_turbsim_parameter_sweep(tmp_path: Path) -> None:
    template = tmp_path / "template.in"
    template.write_text(TEMPLATE)
    output = tmp_path / "Wind"

    paths = generate_cases(
        template,
        output,
        wind_speeds=[8.0, 10.0],
        seeds=[101, 202],
        settings=TurbSimSettings(analysis_time=120.0),
    )

    assert [path.name for path in paths] == [
        "IEA15MW_UMaineSemi_NTM_U8.0_Seed101.in",
        "IEA15MW_UMaineSemi_NTM_U8.0_Seed202.in",
        "IEA15MW_UMaineSemi_NTM_U10.0_Seed101.in",
        "IEA15MW_UMaineSemi_NTM_U10.0_Seed202.in",
    ]
    content = paths[-1].read_text()
    assert "202 RandSeed1" in content
    assert "120.0 AnalysisTime" in content
    assert "48 NumGrid_Z" in content
    assert "48 NumGrid_Y" in content
    assert "296.0 GridHeight" in content
    assert "300.0 GridWidth" in content
    assert "False WrHAWCFF" in content
    assert "Clockwise" not in content
    assert '"NTM" IEC_WindType' in content
    assert "10.0 URef" in content
    assert "50.0 URef" not in content


def test_generate_turbsim_refuses_to_overwrite(tmp_path: Path) -> None:
    template = tmp_path / "template.in"
    template.write_text(TEMPLATE)
    settings = TurbSimSettings()
    generate_cases(template, tmp_path, [10.0], [101], settings)

    with pytest.raises(FileExistsError):
        generate_cases(template, tmp_path, [10.0], [101], settings)
