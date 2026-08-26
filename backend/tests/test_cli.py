from pathlib import Path

from _pytest.capture import CaptureFixture
from _pytest.monkeypatch import MonkeyPatch

from app.cli import main
from app.runs import run_ledger, run_store


def test_strategy_cli_add_list_remove_and_error(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VQD_WORKSPACE", str(tmp_path))
    example = Path(__file__).parents[2] / "examples" / "sma_cross.py"
    assert main(["strategy", "add", str(example)]) == 0
    added = capsys.readouterr()
    assert "Registered user.sma-cross" in added.out
    assert "fingerprint: sha256:" in added.out
    assert main(["strategy", "list"]) == 0
    assert "user.sma-cross" in capsys.readouterr().out
    assert main(["strategy", "remove", "user.sma-cross"]) == 0
    assert "Removed user.sma-cross" in capsys.readouterr().out
    assert main(["strategy", "list"]) == 0
    assert "No user strategies registered" in capsys.readouterr().out
    assert main(["strategy", "remove", "user.missing"]) == 2
    assert "not registered" in capsys.readouterr().err


def test_run_cli_list_show_and_force_delete(
    tmp_path: Path, monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]
) -> None:
    monkeypatch.setenv("VQD_WORKSPACE", str(tmp_path))
    run_store.use_workspace(tmp_path)
    result = run_ledger.create(
        strategy_id="pairs-trading",
        dataset_id="pairs-sample-v1",
        parameters={"lookback": 5, "entry_z": 1.0, "exit_z": 0.25},
        research_cutoff=None,
    )
    run_id = result.manifest.run_id
    assert main(["run", "list"]) == 0
    assert f"{run_id}\tCOMPLETED" in capsys.readouterr().out
    assert main(["run", "show", run_id]) == 0
    assert f'"run_id": "{run_id}"' in capsys.readouterr().out
    assert main(["run", "delete", run_id]) == 2
    assert "requires --force" in capsys.readouterr().err
    assert main(["run", "delete", run_id, "--force"]) == 0
    assert f"Deleted {run_id}" in capsys.readouterr().out
