from pathlib import Path

import pytest

from mcfast.models import model_geometry, referenced_files, safe_path


def test_reference_graph(tmp_path):
    (tmp_path / "main.fst").write_text('"module.dat" ModuleFile - linked module\n')
    (tmp_path / "module.dat").write_text("123.0 TowerHt - tower height\n")
    graph = referenced_files(tmp_path, "main.fst")
    assert [node["name"] for node in graph["files"]] == ["main.fst", "module.dat"]
    assert graph["references"][0]["key"] == "ModuleFile"


def test_path_traversal_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside.fst"
    outside.write_text("test")
    with pytest.raises(ValueError):
        safe_path(tmp_path, "../outside.fst")
    outside.unlink()


def test_model_geometry_loads_first_order_wamit_gdf(tmp_path):
    (tmp_path / "main.fst").write_text('"Hydro.dat" HydroFile - hydrodynamics\n')
    (tmp_path / "Hydro.dat").write_text('"HydroData/body" PotFile - WAMIT root\n')
    wamit = tmp_path / "HydroData" / "wamit_inputs_1stOrder"
    wamit.mkdir(parents=True)
    (wamit / "body.gdf").write_text(
        "test panel\n"
        "1.0 9.81 ULEN GRAV\n"
        "0 1 ISX ISY\n"
        "1 NPANC\n"
        "0 0 -1\n"
        "1 0 -1\n"
        "1 2 -1\n"
        "0 2 -1 1\n"
    )

    floater = model_geometry(tmp_path, "main.fst")["floater"]
    assert floater["source"] == "HydroData/wamit_inputs_1stOrder/body.gdf"
    assert floater["panelCount"] == 2
    assert len(floater["indices"]) == 12
    assert {vertex[2] for vertex in floater["vertices"]} == {-2.0, 0.0, 2.0}


def test_model_geometry_omits_floater_without_supported_gdf(tmp_path):
    (tmp_path / "main.fst").write_text('"Hydro.dat" HydroFile - hydrodynamics\n')
    (tmp_path / "Hydro.dat").write_text('"HydroData/missing" PotFile - WAMIT root\n')

    assert model_geometry(tmp_path, "main.fst")["floater"] is None


def test_official_iea15mw_volturnus_model_when_downloaded():
    root = Path(__file__).parents[1] / "models"
    candidates = list(root.glob("IEA-15-240-RWT/**/*UMaineSemi*.fst"))
    if not candidates:
        pytest.skip("run scripts/fetch_iea15mw.py to enable the official-model integration test")
    entry = candidates[0].relative_to(root).as_posix()
    graph = referenced_files(root, entry)
    assert len(graph["files"]) >= 5
    assert any("ElastoDyn" in node["name"] for node in graph["files"])
