"""The oracle is re-exported where the error channels look for it.

Every error channel is defined as a departure from the perfect observation, so
a channel should be able to name the oracle without importing the nuPlan
adapter. This test pins that the re-export is the adapter's own function rather
than a second implementation that could drift from it.
"""

from __future__ import annotations


def test_the_oracle_is_the_adapter_function_itself() -> None:
    """A copy here would drift from the adapter the first time either changed."""

    from aebrisk.nuplan_adapter.scenario import oracle_world_frame as adapter_function
    from aebrisk.observation.oracle import oracle_world_frame as exported

    assert exported is adapter_function


def test_the_module_declares_what_it_exports() -> None:
    """`from ... import *` on a re-export module should not pull in its imports."""

    from aebrisk.observation import oracle

    assert oracle.__all__ == ["oracle_world_frame"]
