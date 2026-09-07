"""The figures command exposes deterministic evidence-only SVG generation."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner


def test_figures_command_refuses_missing_evidence(tmp_path: Path) -> None:
    from aebrisk.cli.app import app

    result = CliRunner().invoke(
        app,
        [
            "figures",
            "--evidence-dir",
            str(tmp_path / "absent"),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    assert result.exit_code == 1
    assert "figures failed" in result.output


def test_figures_command_reports_both_outputs(tmp_path: Path, monkeypatch) -> None:
    from aebrisk.cli import figures
    from aebrisk.cli.app import app

    first = tmp_path / "one.svg"
    second = tmp_path / "two.svg"
    monkeypatch.setattr(figures, "write_figures", lambda *_: (first, second))
    result = CliRunner().invoke(
        app,
        ["figures", "--evidence-dir", str(tmp_path), "--output-dir", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert str(first) in result.output
    assert str(second) in result.output
