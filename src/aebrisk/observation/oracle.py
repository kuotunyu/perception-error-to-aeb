"""The zero-error observation, named where the error channels will look for it.

`oracle_world_frame` lives in the adapter because reading a scenario is the
adapter's job. It is re-exported here because every error channel is defined as
a departure from the oracle, and a channel should not have to import the nuPlan
adapter to say so.
"""

from __future__ import annotations

from aebrisk.nuplan_adapter.scenario import oracle_world_frame

__all__ = ["oracle_world_frame"]
