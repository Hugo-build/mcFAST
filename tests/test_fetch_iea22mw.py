from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tarfile
from types import ModuleType

import pytest


def load_fetcher() -> ModuleType:
    script = Path(__file__).parents[1] / "scripts" / "fetch_iea22mw.py"
    spec = importlib.util.spec_from_file_location("fetch_iea22mw", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch_iea22mw = load_fetcher()


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def archive_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, contents in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    return buffer.getvalue()


def test_fetch_extracts_only_openfast_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = "IEA-22-280-RWT-1.1.0/"
    payload = archive_bytes(
        {
            prefix + "README.md": b"upstream documentation",
            prefix + "OpenFAST/IEA-22-280-RWT-Monopile/model.fst": b"monopile",
            prefix + "OpenFAST/IEA-22-280-RWT-Semi/model.fst": b"semi",
            prefix + "OpenFAST/IEA-22-280-RWT/shared.dat": b"shared",
        }
    )
    requests: list[tuple[str, int]] = []

    def fake_urlopen(url: str, timeout: int) -> FakeResponse:
        requests.append((url, timeout))
        return FakeResponse(payload)

    monkeypatch.setattr(fetch_iea22mw, "urlopen", fake_urlopen)

    target = fetch_iea22mw.fetch(tmp_path / "models")

    assert requests == [(fetch_iea22mw.URL, 90)]
    assert (target / "IEA-22-280-RWT-Monopile/model.fst").read_bytes() == b"monopile"
    assert (target / "IEA-22-280-RWT-Semi/model.fst").read_bytes() == b"semi"
    assert (target / "IEA-22-280-RWT/shared.dat").read_bytes() == b"shared"
    assert not (target / "README.md").exists()


def test_fetch_preserves_existing_model_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "models" / "IEA-22-280-RWT"
    target.mkdir(parents=True)
    sentinel = target / "existing.fst"
    sentinel.write_text("keep me")

    def unexpected_urlopen(url: str, timeout: int) -> FakeResponse:
        raise AssertionError("existing model should not be downloaded again")

    monkeypatch.setattr(fetch_iea22mw, "urlopen", unexpected_urlopen)

    assert fetch_iea22mw.fetch(tmp_path / "models") == target.resolve()
    assert sentinel.read_text() == "keep me"


def test_fetch_replaces_existing_model_with_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "models" / "IEA-22-280-RWT"
    target.mkdir(parents=True)
    sentinel = target / "old.fst"
    sentinel.write_text("remove me")
    payload = archive_bytes(
        {"IEA-22-280-RWT-1.1.0/OpenFAST/IEA-22-280-RWT-Semi/new.fst": b"new"}
    )
    monkeypatch.setattr(
        fetch_iea22mw,
        "urlopen",
        lambda url, timeout: FakeResponse(payload),
    )

    result = fetch_iea22mw.fetch(tmp_path / "models", force=True)

    assert not sentinel.exists()
    assert (result / "IEA-22-280-RWT-Semi/new.fst").read_bytes() == b"new"


def test_fetch_rejects_archive_without_expected_subtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = archive_bytes({"unexpected/README.md": b"wrong archive"})
    monkeypatch.setattr(
        fetch_iea22mw,
        "urlopen",
        lambda url, timeout: FakeResponse(payload),
    )

    with pytest.raises(RuntimeError, match="expected .*OpenFAST"):
        fetch_iea22mw.fetch(tmp_path / "models")
