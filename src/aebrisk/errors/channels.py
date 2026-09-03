"""Binding the four error channels into the pipeline, with their state.

Until this module existed the channels were implemented, individually tested,
and NEVER CONNECTED: `ChannelStages` was only ever constructed with its identity
defaults, so `apply_error_pipeline` corrupted nothing and every configuration
was the oracle wearing a different name. Every channel's own tests passed
throughout, which is exactly why the first test of this module asserts that a
bound pipeline changes something.

STATE IS PER SCENARIO RUN. Dropout remembers what it drew this step and refuses
to run backwards; fragmentation remembers which tracks are lost and until when.
A pipeline rebuilt each step would redraw fragmentation from scratch and no
track would ever stay lost for its reacquisition delay, so the stages are bound
methods of one object that lives as long as one (scenario, configuration,
replicate) run.

SEVERITY ZERO CORRUPTS NOTHING BUT IS NOT THE ORACLE. Positions, sizes, headings
and visibility come through untouched, but velocity is still a finite difference
of observed positions rather than the oracle's exact value, because differencing
is how a real tracker obtains velocity — it is the estimator, not an error. The
gap between `oracle_aeb` and the all-zero `coalition-none` is therefore a
measurable quantity, and it is why the experiment matrix carries both and why
the Shapley baseline is the latter.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from aebrisk.errors.dropout import DropoutState, apply_dropout
from aebrisk.errors.fragmentation import TrackMemory, update_fragmentation
from aebrisk.errors.latency import LatencySelection, apply_latency
from aebrisk.errors.localization import perturb_localization_shape
from aebrisk.errors.pipeline import (
    ChannelStages,
    ErrorConfiguration,
    load_error_config,
    parameter,
)
from aebrisk.observation.models import TrackState

#: Latency chooses WHICH frame is observed, so it must run first. The same
#: parameters in another order produce a different corruption, and no published
#: number would say which order made it.
STAGE_ORDER: tuple[str, ...] = ("latency", "visibility", "geometry", "tracking")


class ScenarioChannels:
    """The four channels bound to one scenario run, carrying their own state."""

    def __init__(
        self,
        config: ErrorConfiguration,
        error_config: Optional[dict[str, Any]] = None,
        dt_s: float = 0.1,
    ) -> None:
        if config.imported:
            raise ValueError(
                f"{config.configuration_id!r} is an imported configuration; its "
                "severities are measured calibration error rather than this study's "
                "fixed grid, so binding it here would read a 'medium' that means "
                "something else"
            )
        if not isinstance(dt_s, (int, float)) or isinstance(dt_s, bool):
            raise ValueError("dt_s must be a number")
        if not math.isfinite(dt_s) or dt_s <= 0.0:
            raise ValueError(f"dt_s must be finite and positive, got {dt_s!r}")

        self._severities = dict(config.severity_by_channel)
        self._document = load_error_config() if error_config is None else error_config
        self._dt_s = float(dt_s)

        self.dropout_state: Optional[DropoutState] = None
        self.track_memories: dict[str, TrackMemory] = {}
        self.latency_selection: Optional[LatencySelection] = None

    def _parameter(self, channel: str, name: str) -> float:
        return parameter(channel, name, self._severities[channel], self._document)

    def latency(
        self,
        tracks: tuple[TrackState, ...],
        *,
        history: Any,
        current_index: int,
        **_: Any,
    ) -> tuple[TrackState, ...]:
        """Choose which frame the controller sees, and remember what it chose."""

        observed, self.latency_selection = apply_latency(
            history, current_index, self._parameter("latency", "latency_s")
        )
        return observed

    def visibility(
        self,
        tracks: tuple[TrackState, ...],
        *,
        key: Any,
        current_index: int,
        **_: Any,
    ) -> tuple[TrackState, ...]:
        """Hide detections the perception stack never reported."""

        updated, self.dropout_state = apply_dropout(
            tracks,
            self._parameter("dropout", "dropout_probability"),
            key,
            current_index,
            self.dropout_state,
        )
        return updated

    def geometry(
        self,
        tracks: tuple[TrackState, ...],
        *,
        key: Any,
        current_index: int,
        **_: Any,
    ) -> tuple[TrackState, ...]:
        """Move, turn and resize each observation."""

        return perturb_localization_shape(
            tracks,
            position_std_m=self._parameter("localization_shape", "position_std_m"),
            yaw_std_deg=self._parameter("localization_shape", "yaw_std_deg"),
            size_relative_std=self._parameter("localization_shape", "size_relative_std"),
            key=key,
            step=current_index,
        )

    def tracking(
        self,
        tracks: tuple[TrackState, ...],
        *,
        key: Any,
        current_index: int,
        **_: Any,
    ) -> tuple[TrackState, ...]:
        """Break tracks, hold them, return them renamed, and estimate velocity."""

        updated, self.track_memories = update_fragmentation(
            tracks,
            self.track_memories,
            rate_per_s=self._parameter("track_instability", "fragmentation_rate_per_s"),
            reacquisition_delay_s=self._parameter("track_instability", "reacquisition_delay_s"),
            key=key,
            step=current_index,
            dt_s=self._dt_s,
        )
        return updated

    def stages(self) -> ChannelStages:
        """The four stages, bound to this run's state, in the fixed order."""

        return ChannelStages(
            latency=self.latency,
            visibility=self.visibility,
            geometry=self.geometry,
            tracking=self.tracking,
        )
