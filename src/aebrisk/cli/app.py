"""Top-level command-line application.

The real commands (`data`, `simulate`, `evaluate`, `report`) arrive with the
tasks that own them. This module exists from the first commit because the
console entry point is part of the packaging contract: a wheel that installs
but cannot answer `aeb-risk --help` is not installable in any useful sense.

The callback below is what makes that true today. A Typer application with no
command and no callback cannot build a Click command at all, so `--help` raises
instead of printing; the callback also gives every future subcommand one place
to hang shared options from.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    add_completion=False,
    help=(
        "Study how perception errors propagate through a deterministic AEB "
        "controller on a common nuPlan scenario cohort."
    ),
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Run one stage of the AEB error-propagation study.

    Every stage reads a frozen cohort and a fixed protocol and writes a run
    record beside its results, so a number can always be traced back to the
    exact inputs that produced it.
    """


if __name__ == "__main__":
    app()
