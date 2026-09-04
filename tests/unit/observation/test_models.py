"""Contracts for the world-state types every error channel operates on.

These are the study's own types rather than nuPlan's, and that separation is
what makes the error channels testable without a database: a channel takes a
``WorldFrame`` and returns a ``WorldFrame``, so the whole perception pipeline
can be exercised from constructed frames. The adapter's job is to be the only
place that knows what nuPlan calls things.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest


def load_models_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.observation import models
    except ImportError:
        pytest.fail("aebrisk.observation.models is missing", pytrace=False)
    return models


def track_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "track_id": "t-0001",
        "category": "vehicle",
        "center_xy_m": (10.0, 2.0),
        "yaw_rad": 0.0,
        "size_lw_m": (4.5, 1.9),
        "velocity_xy_mps": (5.0, 0.0),
        "visible": True,
        "source_timestamp_us": 1_600_000_000_000_000,
        "covariance_xy": (0.1, 0.0, 0.0, 0.1),
    }
    values.update(overrides)
    return values


def make_track(models: ModuleType, **overrides: Any) -> Any:
    return models.TrackState(**track_values(**overrides))


def frame_values(models: ModuleType, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scenario_token": "s-0001",
        "timestamp_us": 1_600_000_000_000_000,
        "ego_center_xy_m": (0.0, 0.0),
        "ego_yaw_rad": 0.0,
        "ego_speed_mps": 8.0,
        "tracks": (make_track(models),),
    }
    values.update(overrides)
    return values


def test_a_track_state_carries_every_field_a_threat_assessment_needs() -> None:
    """A missing field would have to be invented downstream, silently."""

    models = load_models_module()

    track = make_track(models)

    assert track.track_id == "t-0001"
    assert track.category == "vehicle"
    assert track.center_xy_m == (10.0, 2.0)
    assert track.size_lw_m == (4.5, 1.9)
    assert track.velocity_xy_mps == (5.0, 0.0)
    assert track.visible is True
    assert track.covariance_xy == (0.1, 0.0, 0.0, 0.1)


def test_a_track_state_is_frozen() -> None:
    """Error channels return new frames; mutating one in place would hide the change."""

    import dataclasses

    models = load_models_module()
    track = make_track(models)

    with pytest.raises(dataclasses.FrozenInstanceError):
        track.track_id = "t-0002"  # type: ignore[misc]


@pytest.mark.parametrize("category", ["vehicle", "pedestrian", "bicycle", "object"])
def test_every_observation_category_is_accepted(category: str) -> None:
    """The four groups are what the risk weighting and the AEB priority key on."""

    models = load_models_module()

    assert make_track(models, category=category).category == category


def test_an_unknown_category_is_refused() -> None:
    """A category nobody weighted would be scored as if it were an inert object."""

    models = load_models_module()

    with pytest.raises(ValueError, match=r"^category\ must\ be\ one\ of\ "):
        make_track(models, category="animal")


@pytest.mark.parametrize("field", ["center_xy_m", "velocity_xy_mps", "size_lw_m"])
def test_a_two_component_field_must_have_two_components(field: str) -> None:
    """A three-component position would index wrongly rather than fail loudly."""

    models = load_models_module()

    with pytest.raises(ValueError, match=rf"^{field} must have exactly two components$"):
        make_track(models, **{field: (1.0, 2.0, 3.0)})


def test_the_covariance_must_have_four_components() -> None:
    """The row-major 2x2 layout is what the localization channel writes into."""

    models = load_models_module()

    with pytest.raises(
        ValueError,
        match=r"^covariance_xy (must have exactly four components|components must be finite)$",
    ):
        make_track(models, covariance_xy=(0.1, 0.1))


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")])
def test_a_non_finite_measurement_is_refused(bad_value: float) -> None:
    """A NaN position propagates into every distance and time-to-collision."""

    models = load_models_module()

    with pytest.raises(ValueError):
        make_track(models, center_xy_m=(bad_value, 0.0))


@pytest.mark.parametrize("dimension", [0.0, -1.0])
def test_a_non_positive_box_dimension_is_refused(dimension: float) -> None:
    """A zero-area box can never overlap, so it is a threat that cannot be detected."""

    models = load_models_module()

    with pytest.raises(ValueError, match=r"^size_lw_m\ components\ must\ be\ positive"):
        make_track(models, size_lw_m=(dimension, 1.9))


def test_an_empty_track_id_is_refused() -> None:
    """Tracks are matched across frames by identity; an empty one matches nothing."""

    models = load_models_module()

    with pytest.raises(
        ValueError,
        match=r"^(track_id\ must\ not\ be\ empty|tracks\ contain\ a\ duplicate\ track_id)$",
    ):
        make_track(models, track_id="")


def test_a_world_frame_carries_the_ego_and_its_tracks() -> None:
    """The frame is the single input to every error channel and to the AEB."""

    models = load_models_module()

    frame = models.WorldFrame(**frame_values(models))

    assert frame.scenario_token == "s-0001"
    assert frame.ego_speed_mps == 8.0
    assert len(frame.tracks) == 1


def test_a_world_frame_is_frozen() -> None:
    """Every channel produces a new frame so the original stays available."""

    import dataclasses

    models = load_models_module()
    frame = models.WorldFrame(**frame_values(models))

    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.timestamp_us = 0  # type: ignore[misc]


def test_tracks_are_ordered_by_track_id() -> None:
    """Iteration order reaches the AEB's tie-breaking, so it must not depend on input order."""

    models = load_models_module()
    tracks = (
        make_track(models, track_id="t-0003"),
        make_track(models, track_id="t-0001"),
        make_track(models, track_id="t-0002"),
    )

    frame = models.WorldFrame(**frame_values(models, tracks=tracks))

    assert [track.track_id for track in frame.tracks] == ["t-0001", "t-0002", "t-0003"]


def test_duplicate_track_ids_are_refused() -> None:
    """Two tracks with one identity would be matched to each other across frames."""

    models = load_models_module()
    tracks = (make_track(models, track_id="t-0001"), make_track(models, track_id="t-0001"))

    with pytest.raises(ValueError, match=r"^tracks contain a duplicate track_id$"):
        models.WorldFrame(**frame_values(models, tracks=tracks))


def test_a_frame_with_no_tracks_is_valid() -> None:
    """An empty road is a legitimate observation, and dropout can produce one."""

    models = load_models_module()

    frame = models.WorldFrame(**frame_values(models, tracks=()))

    assert frame.tracks == ()


def test_a_negative_ego_speed_is_refused() -> None:
    """Speed is a magnitude; reversing is expressed by the velocity vector."""

    models = load_models_module()

    with pytest.raises(
        ValueError, match=r"^ego_speed_mps\ must\ be\ a\ finite,\ non\-negative\ magnitude$"
    ):
        models.WorldFrame(**frame_values(models, ego_speed_mps=-1.0))


def test_a_negative_timestamp_is_refused() -> None:
    """Timestamps order the simulation; a negative one would sort before every frame."""

    models = load_models_module()

    with pytest.raises(
        ValueError,
        match=r"^(source_timestamp_us\ must\ not\ be\ negative|timestamp_us\ must\ not\ be\ negative)$",
    ):
        models.WorldFrame(**frame_values(models, timestamp_us=-1))


def test_a_non_finite_yaw_is_refused() -> None:
    """A NaN heading makes every box corner, and so every overlap test, undefined."""

    models = load_models_module()

    with pytest.raises(
        ValueError, match=r"^(ego_yaw_rad\ must\ be\ finite|yaw_rad\ must\ be\ finite)$"
    ):
        make_track(models, yaw_rad=float("nan"))


def test_a_non_finite_covariance_is_refused() -> None:
    """The localization channel writes into this; a NaN there spreads to the estimate."""

    models = load_models_module()

    with pytest.raises(
        ValueError,
        match=r"^covariance_xy (must have exactly four components|components must be finite)$",
    ):
        make_track(models, covariance_xy=(float("inf"), 0.0, 0.0, 0.1))


def test_a_negative_track_timestamp_is_refused() -> None:
    """The latency channel dates observations; a negative stamp would look newest."""

    models = load_models_module()

    with pytest.raises(ValueError, match=r"^source_timestamp_us\ must\ not\ be\ negative"):
        make_track(models, source_timestamp_us=-1)


def test_an_empty_scenario_token_is_refused() -> None:
    """A frame nobody can trace to a scenario cannot be aggregated with the cohort."""

    models = load_models_module()

    with pytest.raises(ValueError, match=r"^scenario_token must not be empty$"):
        models.WorldFrame(**frame_values(models, scenario_token=""))


def test_a_non_finite_ego_yaw_is_refused() -> None:
    """The ego's heading orients its own footprint against every threat."""

    models = load_models_module()

    with pytest.raises(ValueError, match=r"^ego_yaw_rad\ must\ be\ finite"):
        models.WorldFrame(**frame_values(models, ego_yaw_rad=float("nan")))
