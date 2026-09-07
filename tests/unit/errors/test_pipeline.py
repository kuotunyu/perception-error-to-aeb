"""Contracts for the deterministic error pipeline every channel plugs into.

Three properties matter here, and the rest of the study rests on them.

The randomness must be a pure function of the key. A run repeated tomorrow on
another machine has to corrupt the same observations in the same way, or the
factorial attribution is comparing two different experiments.

The all-zero configuration must be the exact identity. It is the reference every
other severity is measured against, so a `zero` that perturbs anything at all
moves the baseline without saying so.

The channel order must be fixed and observable. Applying latency after dropout
rather than before produces a different corruption from the same parameters, and
nothing downstream could tell which order produced a published number.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest

PROTOCOL_HASH = "a" * 64


def load_pipeline_module() -> ModuleType:
    """Import inside the test so a missing module is a purposeful RED failure."""

    try:
        from aebrisk.errors import pipeline
    except ImportError:
        pytest.fail("aebrisk.errors.pipeline is missing", pytrace=False)
    return pipeline


def key_values(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "scenario_token": "s-0001",
        "channel": "dropout",
        "severity": "medium",
        "replicate": 0,
        "protocol_hash": PROTOCOL_HASH,
    }
    values.update(overrides)
    return values


def make_key(pipeline: ModuleType, **overrides: Any) -> Any:
    return pipeline.ErrorKey(**key_values(**overrides))


def make_frame(index: int = 0, tracks: int = 2) -> Any:
    from aebrisk.observation.models import TrackState, WorldFrame

    return WorldFrame(
        scenario_token="s-0001",
        timestamp_us=1_600_000_000_000_000 + index * 100_000,
        ego_center_xy_m=(float(index), 0.0),
        ego_yaw_rad=0.0,
        ego_speed_mps=8.0,
        tracks=tuple(
            TrackState(
                track_id=f"t-{number:04d}",
                category="vehicle",
                center_xy_m=(10.0 + number + index, 2.0),
                yaw_rad=0.1,
                size_lw_m=(4.5, 1.9),
                velocity_xy_mps=(5.0, 0.0),
                visible=True,
                source_timestamp_us=1_600_000_000_000_000 + index * 100_000,
                covariance_xy=(0.0, 0.0, 0.0, 0.0),
            )
            for number in range(tracks)
        ),
    )


def zero_configuration(pipeline: ModuleType) -> Any:
    return pipeline.ErrorConfiguration(
        configuration_id="all-zero",
        severity_by_channel=dict.fromkeys(pipeline.ERROR_CHANNELS, "zero"),
    )


def test_the_key_carries_everything_that_makes_a_draw_unique() -> None:
    """Two runs that share a key must corrupt identically, and only then."""

    pipeline = load_pipeline_module()

    key = make_key(pipeline)

    assert key.scenario_token == "s-0001"
    assert key.channel == "dropout"
    assert key.severity == "medium"
    assert key.replicate == 0
    assert key.protocol_hash == PROTOCOL_HASH


def test_the_key_is_frozen() -> None:
    """A key edited after a draw would make the draw unreproducible."""

    import dataclasses

    pipeline = load_pipeline_module()
    key = make_key(pipeline)

    with pytest.raises(dataclasses.FrozenInstanceError):
        key.replicate = 1  # type: ignore[misc]


def test_the_seed_is_stable_for_one_key() -> None:
    """The same key must give the same seed in every process, forever."""

    pipeline = load_pipeline_module()

    assert pipeline.keyed_seed(make_key(pipeline)) == pipeline.keyed_seed(make_key(pipeline))


def test_the_seed_is_the_documented_value_for_a_fixed_key() -> None:
    """Pinning one value is what stops the derivation drifting silently.

    Without this, any change to the canonical form would still look
    deterministic while producing an entirely different experiment.
    """

    import hashlib
    import json

    pipeline = load_pipeline_module()
    canonical = json.dumps(key_values(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected = int.from_bytes(hashlib.sha256(canonical).digest()[:8], "big")

    pipeline.keyed_seed.cache_clear()
    assert pipeline.keyed_seed(make_key(pipeline)) == expected
    assert pipeline.keyed_seed(make_key(pipeline)) == expected


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("scenario_token", "s-0002"),
        ("channel", "latency"),
        ("severity", "high"),
        ("replicate", 1),
        ("protocol_hash", "b" * 64),
    ],
)
def test_every_key_field_changes_the_seed(field: str, value: Any) -> None:
    """A field that does not reach the seed is a dimension of the study that is not varied."""

    pipeline = load_pipeline_module()

    baseline = pipeline.keyed_seed(make_key(pipeline))

    assert pipeline.keyed_seed(make_key(pipeline, **{field: value})) != baseline


def test_the_seed_fits_in_sixty_four_bits() -> None:
    """The generator takes a 64-bit seed; a wider one would be truncated somewhere unstated."""

    pipeline = load_pipeline_module()

    assert 0 <= pipeline.keyed_seed(make_key(pipeline)) < 2**64


def test_a_missing_protocol_hash_is_refused() -> None:
    """Without it, two protocols would draw the same corruption and look comparable."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError, match=r"^protocol_hash\ must\ be\ a\ 64\-character\ SHA\-256\ digest$"
    ):
        make_key(pipeline, protocol_hash="")


def test_a_malformed_protocol_hash_is_refused() -> None:
    """A truncated hash would still seed, and would still look deterministic."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError, match=r"^protocol_hash\ must\ be\ a\ 64\-character\ SHA\-256\ digest$"
    ):
        make_key(pipeline, protocol_hash="a" * 63)


def test_an_unknown_channel_is_refused() -> None:
    """A channel nobody implemented would silently contribute nothing to the attribution."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^channel must be one of "):
        make_key(pipeline, channel="weather")


def test_an_unknown_severity_is_refused() -> None:
    """The four levels are the experiment's axis; a fifth is a different study."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^severity must be one of "):
        make_key(pipeline, severity="extreme")


def test_a_negative_replicate_is_refused() -> None:
    """Replicates are counted from zero; a negative one indexes nothing."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError, match=r"^(replicate\ must\ be\ an\ integer|replicate\ must\ not\ be\ negative)$"
    ):
        make_key(pipeline, replicate=-1)


def test_the_generator_is_local_to_the_key() -> None:
    """Touching the global RNG would make one channel's draws depend on another's."""

    import numpy as np

    pipeline = load_pipeline_module()
    np.random.seed(12345)
    before = np.random.random()
    np.random.seed(12345)

    pipeline.generator_for(make_key(pipeline)).random()

    assert np.random.random() == before


def test_two_generators_from_one_key_agree() -> None:
    """A generator that depends on when it was built is not keyed at all."""

    pipeline = load_pipeline_module()
    key = make_key(pipeline)

    first = pipeline.generator_for(key).random(5).tolist()
    second = pipeline.generator_for(key).random(5).tolist()

    assert first == second


def test_the_reference_configuration_validates() -> None:
    """The committed config is what every formal run reads; it must load."""

    from pathlib import Path

    import yaml

    pipeline = load_pipeline_module()
    path = Path(__file__).resolve().parents[3] / "configs" / "errors" / "formal_v1.yaml"

    pipeline.validate_error_config(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_every_channel_must_be_configured() -> None:
    """A missing channel would be run at no severity and reported as if it were."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    del config["channels"]["latency"]

    with pytest.raises(ValueError, match=r"^missing error channels in configuration: "):
        pipeline.validate_error_config(config)


def test_the_channels_mapping_itself_is_required() -> None:
    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    del config["channels"]

    with pytest.raises(ValueError, match=r"^missing error channels in configuration: "):
        pipeline.validate_error_config(config)


def test_an_unknown_channel_in_the_config_is_refused() -> None:
    """A channel the pipeline never applies would look configured and do nothing."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["weather"] = {"rain_mm": [0.0, 1.0, 2.0, 3.0]}

    with pytest.raises(ValueError, match=r"^unknown error channels in configuration: "):
        pipeline.validate_error_config(config)


def test_every_parameter_needs_one_value_per_severity() -> None:
    """A short list would make the highest severity silently reuse a lower one."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["dropout"]["dropout_probability"] = [0.0, 0.05, 0.10]

    with pytest.raises(ValueError, match=r"^dropout_probability must give one value per severity "):
        pipeline.validate_error_config(config)


def test_the_zero_severity_must_be_exactly_zero() -> None:
    """This is the load-bearing one: `zero` is the reference the study compares to.

    A `zero` of 0.001 would still look like a baseline and would move every
    measured effect by an amount nothing in the results could reveal.
    """

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["localization_shape"]["position_std_m"] = [0.001, 0.25, 0.50, 1.00]

    with pytest.raises(
        ValueError,
        match=r"^position_std_m at severity zero must be exactly 0; zero is the reference every other severity is measured against$",
    ):
        pipeline.validate_error_config(config)


@pytest.mark.parametrize("bad_value", [-0.1, float("nan"), float("inf")])
def test_an_impossible_parameter_value_is_refused(bad_value: float) -> None:
    """Every parameter here is a magnitude; a negative or NaN one is a typo."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["latency"]["latency_s"] = [0.0, 0.10, bad_value, 0.40]

    with pytest.raises(ValueError, match=r"^latency_s values must be finite and non-negative$"):
        pipeline.validate_error_config(config)


def test_the_severity_list_is_the_declared_one() -> None:
    """Reordering the severities would relabel every configuration in the matrix."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["severities"] = ["zero", "medium", "low", "high"]

    with pytest.raises(ValueError, match=r"^severities\ must\ be\ exactly\ "):
        pipeline.validate_error_config(config)


def test_the_severity_list_itself_is_required() -> None:
    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    del config["severities"]

    with pytest.raises(ValueError, match=r"^severities must be exactly "):
        pipeline.validate_error_config(config)


def test_a_parameter_value_can_be_looked_up_by_channel_and_severity() -> None:
    """This is how a channel asks the configuration what it should do."""

    pipeline = load_pipeline_module()

    assert pipeline.parameter("dropout", "dropout_probability", "medium") == 0.10
    assert pipeline.parameter("latency", "latency_s", "zero") == 0.0


def test_the_all_zero_pipeline_returns_the_oracle_unchanged() -> None:
    """The identity must be exact, not approximate, or the baseline is not a baseline."""

    pipeline = load_pipeline_module()
    history = tuple(make_frame(index) for index in range(3))

    tracks = pipeline.apply_error_pipeline(
        history, 2, zero_configuration(pipeline), make_key(pipeline, severity="zero")
    )

    assert tracks == history[2].tracks


def test_the_all_zero_pipeline_preserves_order_and_covariance() -> None:
    """Order reaches the AEB's tie-breaking and covariance reaches the threat estimate."""

    pipeline = load_pipeline_module()
    history = tuple(make_frame(index, tracks=4) for index in range(2))

    tracks = pipeline.apply_error_pipeline(
        history, 1, zero_configuration(pipeline), make_key(pipeline, severity="zero")
    )

    assert [track.track_id for track in tracks] == ["t-0000", "t-0001", "t-0002", "t-0003"]
    assert all(track.covariance_xy == (0.0, 0.0, 0.0, 0.0) for track in tracks)
    assert all(track.visible for track in tracks)


def test_the_channels_are_applied_in_the_declared_order() -> None:
    """Latency then visibility then geometry then tracking; any other order is another study.

    The same parameters applied in a different order produce a different
    corruption, and nothing in a published number would say which order made it.
    """

    pipeline = load_pipeline_module()
    calls: list[str] = []

    def spy(name: str) -> Any:
        def channel(tracks: Any, **kwargs: Any) -> Any:
            calls.append(name)
            return tracks

        return channel

    history = tuple(make_frame(index) for index in range(2))
    stages = pipeline.ChannelStages(
        latency=spy("latency"),
        visibility=spy("visibility"),
        geometry=spy("geometry"),
        tracking=spy("tracking"),
    )

    pipeline.apply_error_pipeline(
        history, 1, zero_configuration(pipeline), make_key(pipeline), stages=stages
    )

    assert calls == ["latency", "visibility", "geometry", "tracking"]


def test_each_stage_receives_the_supplied_configuration_in_its_context() -> None:
    """A config-aware stage must see the exact configuration the public call received."""

    pipeline = load_pipeline_module()
    config = zero_configuration(pipeline)
    seen: list[Any] = []

    def config_aware(tracks: Any, **context: Any) -> Any:
        seen.append(context["config"])
        return tracks

    stages = pipeline.ChannelStages(
        latency=config_aware,
        visibility=config_aware,
        geometry=config_aware,
        tracking=config_aware,
    )
    pipeline.apply_error_pipeline(
        (make_frame(),),
        0,
        config,
        make_key(pipeline),
        stages=stages,
    )

    assert seen == [config, config, config, config]


def test_the_pipeline_refuses_an_index_outside_the_history() -> None:
    """Reading past the end would silently reuse the last frame as if it were current."""

    pipeline = load_pipeline_module()
    history = tuple(make_frame(index) for index in range(2))

    with pytest.raises(IndexError, match=r"^current_index\ "):
        pipeline.apply_error_pipeline(history, 2, zero_configuration(pipeline), make_key(pipeline))


def test_the_pipeline_refuses_an_empty_history() -> None:
    """There is no observation to corrupt, so returning an empty tuple would be a lie."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError, match=r"^(current_index\ |history\ must\ contain\ at\ least\ one\ frame)"
    ):
        pipeline.apply_error_pipeline((), 0, zero_configuration(pipeline), make_key(pipeline))


def test_a_configuration_must_name_a_severity_for_every_channel() -> None:
    """An unconfigured channel would run at whatever the default happened to be."""

    pipeline = load_pipeline_module()
    configuration = pipeline.ErrorConfiguration(
        configuration_id="partial",
        severity_by_channel={"dropout": "low"},
    )
    history = (make_frame(0),)

    with pytest.raises(
        ValueError, match=r"^configuration does not name a severity for every channel: "
    ):
        pipeline.apply_error_pipeline(history, 0, configuration, make_key(pipeline))


def test_the_standard_configuration_identifiers_are_generated_not_typed() -> None:
    """Every configuration in the matrix must be nameable the same way."""

    pipeline = load_pipeline_module()

    assert pipeline.configuration_id("dropout", "medium") == "dropout-medium"


def test_an_imported_calibration_configuration_lives_in_its_own_namespace() -> None:
    """P2's artifacts may add configurations; they may never redefine the study's own.

    A calibration import that could produce `dropout-medium` would silently
    change what the medium severity means, and every comparison against it.
    """

    pipeline = load_pipeline_module()

    imported = pipeline.imported_configuration_id("identity-corrector")

    assert imported == "calibration_imported_identity-corrector"
    assert imported not in {
        pipeline.configuration_id(channel, severity)
        for channel in pipeline.ERROR_CHANNELS
        for severity in pipeline.SEVERITIES
    }


def test_an_imported_configuration_may_not_claim_a_standard_name() -> None:
    """The namespace guard has to refuse, not merely prefix and hope."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError,
        match=r"^configuration_id\ namespace\ disagrees\ with\ `imported`:\ an\ imported\ ",
    ):
        pipeline.ErrorConfiguration(
            configuration_id="calibration_imported_x",
            severity_by_channel=dict.fromkeys(pipeline.ERROR_CHANNELS, "zero"),
            imported=False,
        )


def test_an_imported_configuration_must_carry_the_prefix() -> None:
    """Both directions, or the guard only catches the mistake nobody makes."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError,
        match=r"^configuration_id\ namespace\ disagrees\ with\ `imported`:\ an\ imported\ ",
    ):
        pipeline.ErrorConfiguration(
            configuration_id="dropout-medium",
            severity_by_channel=dict.fromkeys(pipeline.ERROR_CHANNELS, "zero"),
            imported=True,
        )


def test_an_empty_scenario_token_is_refused() -> None:
    """A key that names no scenario would give every scenario the same corruption."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^scenario_token must not be empty$"):
        make_key(pipeline, scenario_token="")


def test_a_non_integer_replicate_is_refused() -> None:
    """`True` is an int in Python, and it would silently mean replicate one."""

    pipeline = load_pipeline_module()

    with pytest.raises(
        ValueError, match=r"^(replicate\ must\ be\ an\ integer|replicate\ must\ not\ be\ negative)$"
    ):
        make_key(pipeline, replicate=True)


def test_an_empty_configuration_identifier_is_refused() -> None:
    """A result must name the configuration that produced it."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^configuration_id must not be empty$"):
        pipeline.ErrorConfiguration(
            configuration_id="",
            severity_by_channel=dict.fromkeys(pipeline.ERROR_CHANNELS, "zero"),
        )


def test_a_configuration_naming_an_unknown_channel_is_refused() -> None:
    """A channel the pipeline never applies would look configured and do nothing."""

    pipeline = load_pipeline_module()
    severities = dict.fromkeys(pipeline.ERROR_CHANNELS, "zero")
    severities["weather"] = "high"

    with pytest.raises(ValueError, match=r"^unknown channels in configuration: "):
        pipeline.ErrorConfiguration(configuration_id="odd", severity_by_channel=severities)


def test_a_configuration_naming_an_unknown_severity_is_refused() -> None:
    """A fifth level would be run and reported as one of the declared four."""

    pipeline = load_pipeline_module()
    severities = dict.fromkeys(pipeline.ERROR_CHANNELS, "zero")
    severities["dropout"] = "extreme"

    with pytest.raises(ValueError, match=r"^channel .* has an unknown severity "):
        pipeline.ErrorConfiguration(configuration_id="odd", severity_by_channel=severities)


def test_an_unknown_parameter_in_a_channel_is_refused() -> None:
    """A parameter nothing reads is a knob somebody expects to have an effect."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["dropout"]["rain_mm"] = [0.0, 1.0, 2.0, 3.0]

    with pytest.raises(ValueError, match=r"^channel .* has unknown parameters: "):
        pipeline.validate_error_config(config)


def test_a_missing_parameter_in_a_channel_is_refused() -> None:
    """A channel reading a parameter the config omits would fail mid-simulation."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    del config["channels"]["localization_shape"]["yaw_std_deg"]

    with pytest.raises(ValueError, match=r"^channel \'\w+\' is missing parameters: "):
        pipeline.validate_error_config(config)


def test_a_non_numeric_parameter_value_is_refused() -> None:
    """A string that looks like a number would compare and scale in surprising ways."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["latency"]["latency_s"] = [0.0, "0.10", 0.20, 0.40]

    with pytest.raises(ValueError, match=r"^latency_s values must be numbers$"):
        pipeline.validate_error_config(config)


def test_a_boolean_parameter_value_is_refused() -> None:
    """`True` is a number in Python, and it would silently mean one second."""

    pipeline = load_pipeline_module()
    config = pipeline.load_error_config()
    config["channels"]["latency"]["latency_s"] = [0.0, True, 0.20, 0.40]

    with pytest.raises(ValueError, match=r"^latency_s values must be numbers$"):
        pipeline.validate_error_config(config)


def test_a_configuration_identifier_refuses_an_unknown_channel() -> None:
    """Generating a name for a channel that does not exist would name nothing."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^unknown channel: "):
        pipeline.configuration_id("weather", "high")


def test_a_configuration_identifier_refuses_an_unknown_severity() -> None:
    """The same failure on the other axis of the matrix."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^unknown severity: |^severity must be one of "):
        pipeline.configuration_id("dropout", "extreme")


def test_an_imported_configuration_must_name_its_source() -> None:
    """An imported configuration nobody can trace back to an artifact is untraceable."""

    pipeline = load_pipeline_module()

    with pytest.raises(ValueError, match=r"^an\ imported\ configuration\ must\ name\ its\ source$"):
        pipeline.imported_configuration_id("")
