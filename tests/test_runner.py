import json
from pathlib import Path

from mcfast import runner


def test_find_openfast_prefers_active_environment(monkeypatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    executable = bin_dir / "openfast"
    executable.write_text("#!/bin/sh\n")
    executable.chmod(0o755)

    monkeypatch.setattr(runner.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/system/openfast")

    assert runner.find_openfast() == str(executable)


def test_find_openfast_falls_back_to_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner.sys, "prefix", str(tmp_path))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/system/openfast")

    assert runner.find_openfast() == "/system/openfast"


def test_find_turbsim_uses_project_bundle_before_path(monkeypatch, tmp_path: Path) -> None:
    bundled = tmp_path / ".openfast" / "conda-4.2.1" / "bin" / "turbsim"
    bundled.parent.mkdir(parents=True)
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runner.sys, "prefix", str(tmp_path / "environment"))
    monkeypatch.setattr(runner.shutil, "which", lambda _: "/system/turbsim")

    assert runner.find_turbsim() == str(bundled)


def test_run_openfast_saves_console_manifest_and_outputs(tmp_path: Path) -> None:
    model = tmp_path / "Example.fst"
    model.write_text("example deck\n")
    executable = tmp_path / "openfast"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"-v\" ]; then echo 'OpenFAST-v4.2.1'; exit 0; fi\n"
        "echo 'simulation started'\n"
        "echo 'simulation warning' >&2\n"
        "stem=${1%.fst}\n"
        "echo result > \"${stem}.outb\"\n"
    )
    executable.chmod(0o755)

    return_code, run_dir = runner.run_openfast(
        model.resolve(),
        str(executable),
        tmp_path / "results",
        "test-run",
    )

    assert return_code == 0
    assert "simulation started" in (run_dir / "console.log").read_text()
    assert "simulation warning" in (run_dir / "console.log").read_text()
    assert (run_dir / "Example.outb").read_text() == "result\n"
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["openfast_version"] == "OpenFAST-v4.2.1"
    assert not list(tmp_path.glob(".mcfast-*"))
