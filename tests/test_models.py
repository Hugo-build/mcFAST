from pathlib import Path

import pytest

from mcfast.models import referenced_files, safe_path


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


def test_official_iea15mw_volturnus_model_when_downloaded():
    root = Path(__file__).parents[1] / "models"
    candidates = list(root.glob("IEA-15-240-RWT/**/*UMaineSemi*.fst"))
    if not candidates:
        pytest.skip("run scripts/fetch_iea15mw.py to enable the official-model integration test")
    entry = candidates[0].relative_to(root).as_posix()
    graph = referenced_files(root, entry)
    assert len(graph["files"]) >= 5
    assert any("ElastoDyn" in node["name"] for node in graph["files"])

