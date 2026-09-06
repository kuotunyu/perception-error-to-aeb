"""The deterministic frame every perception error channel plugs into.

Three decisions here carry the rest of the study.

**The randomness is a pure function of the key.** A configuration rerun next
month on another machine has to corrupt the same observations in the same way,
or the factorial attribution is differencing two different experiments rather
than two conditions. The seed comes from a hash of the whole key, and every
channel draws from its own generator built from that seed. Nothing touches the
global numpy RNG, because a global stream makes one channel's draws depend on
how many draws another channel happened to make first.

**The all-zero configuration is the exact identity.** It is the reference every
other severity is measured against, so it must return the oracle's tracks
unchanged, in the same order, with the same covariance. A `zero` that perturbed
anything would move the baseline without appearing in any result.

**The channel order is fixed and observable.** Latency chooses *which* frame is
observed, so it runs first and everything after it operates on that frame.
Visibility decides what is reported at all. Geometry corrupts what survived.
Tracking then decides what identity it carries. The same parameters in a
different order produce a different corruption, and no published number would
say which order made it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import yaml

from aebrisk.observation.models import TrackState, WorldFrame

ERROR_CHANNELS: tuple[str, ...] = (
    "dropout",
    "localization_shape",
    "latency",
    "track_instability",
)
SEVERITIES: tuple[str, ...] = ("zero", "low", "medium", "high")

#: The parameters each channel reads, in the order they appear in the config.
CHANNEL_PARAMETERS: dict[str, tuple[str, ...]] = {
    "dropout": ("dropout_probability",),
    "localization_shape": ("position_std_m", "yaw_std_deg", "size_relative_std"),
    "latency": ("latency_s",),
    "track_instability": ("fragmentation_rate_per_s", "reacquisition_delay_s"),
}

#: Configurations built from a `bev-calibration-lab` artifact live under this
#: prefix and can never collide with the study's own severity configurations.
#: Without the separation, an imported artifact could redefine what `medium`
#: means and every comparison against it would move silently.
IMPORTED_PREFIX = "calibration_imported_"

CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "errors" / "formal_v1.yaml"

Stage = Callable[..., tuple[TrackState, ...]]


@dataclass(frozen=True)
class ErrorKey:
    """Everything that makes one channel's corruption of one scenario unique."""

    scenario_token: str
    channel: str
    severity: str
    replicate: int
    protocol_hash: str

    def __post_init__(self) -> None:
        if not self.scenario_token:
            raise ValueError("scenario_token must not be empty")
        if self.channel not in ERROR_CHANNELS:
            raise ValueError(f"channel must be one of {ERROR_CHANNELS}, got {self.channel!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity!r}")
        if isinstance(self.replicate, bool) or not isinstance(self.replicate, int):
            raise ValueError("replicate must be an integer")
        if self.replicate < 0:
            raise ValueError("replicate must not be negative")
        if len(self.protocol_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.protocol_hash
        ):
            raise ValueError("protocol_hash must be a 64-character SHA-256 digest")


@dataclass(frozen=True)
class ErrorConfiguration:
    """One point in the experiment matrix: a severity for every channel."""

    configuration_id: str
    severity_by_channel: Mapping[str, str]
    imported: bool = False

    def __post_init__(self) -> None:
        if not self.configuration_id:
            raise ValueError("configuration_id must not be empty")
        has_prefix = self.configuration_id.startswith(IMPORTED_PREFIX)
        if has_prefix != self.imported:
            raise ValueError(
                f"configuration_id namespace disagrees with `imported`: an imported "
                f"configuration must start with {IMPORTED_PREFIX!r} and a study "
                f"configuration must not, got {self.configuration_id!r}"
            )
        unknown = sorted(set(self.severity_by_channel) - set(ERROR_CHANNELS))
        if unknown:
            raise ValueError(f"unknown channels in configuration: {unknown}")
        for channel, severity in self.severity_by_channel.items():
            if severity not in SEVERITIES:
                raise ValueError(f"channel {channel!r} has an unknown severity {severity!r}")


@dataclass(frozen=True)
class ChannelStages:
    """The four stages, injectable so the order can be observed and each tested alone.

    They default to the identity because the channels themselves arrive in the
    tasks that own them. Defaulting to identity rather than to a stub that
    raises keeps the all-zero pipeline meaningful before any channel exists.
    """

    latency: Stage = field(default=lambda tracks, **kwargs: tracks)
    visibility: Stage = field(default=lambda tracks, **kwargs: tracks)
    geometry: Stage = field(default=lambda tracks, **kwargs: tracks)
    tracking: Stage = field(default=lambda tracks, **kwargs: tracks)


def _canonical_key(key: ErrorKey) -> bytes:
    payload = {
        "scenario_token": key.scenario_token,
        "channel": key.channel,
        "severity": key.severity,
        "replicate": key.replicate,
        "protocol_hash": key.protocol_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@lru_cache(maxsize=4096)
def keyed_seed(key: ErrorKey) -> int:
    """Derive this key's 64-bit seed.

    The first eight bytes of the SHA-256 of the canonical key, big endian. The
    canonical form is pinned by a test that recomputes it independently, because
    a change to the serialization would still look deterministic while producing
    a different experiment.

    Memoised because it is asked the same question hundreds of thousands of times
    in a single run — once per track, per field, per step — and the answer depends
    only on the key, which is one object for the whole run. The cache changes no
    byte of the result: an `ErrorKey` is frozen, so two equal keys are the same
    question.
    """

    return int.from_bytes(hashlib.sha256(_canonical_key(key)).digest()[:8], "big")


def generator_for(key: ErrorKey) -> np.random.Generator:
    """Build this key's own generator, never the global one."""

    return np.random.Generator(np.random.PCG64(keyed_seed(key)))


def track_field_generator(
    key: ErrorKey,
    track_id: str,
    step: int,
    field: str,
) -> np.random.Generator:
    """Build the generator for one track's one field at one step.

    Every channel draws this way rather than from a shared sequential stream.
    A sequential stream would make one track's corruption depend on how many
    tracks and fields happened to be processed first, so adding an unrelated
    object at the edge of the scene would change the error applied to the one in
    front. That is a simulator artefact, not a perception error, and it would
    not appear in any result.
    """

    payload = json.dumps(
        {"seed": keyed_seed(key), "track_id": track_id, "step": step, "field": field},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")
    return np.random.Generator(np.random.PCG64(seed))


def load_error_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Read the committed error configuration."""

    source = CONFIG_PATH if path is None else path
    document: dict[str, Any] = yaml.safe_load(source.read_text(encoding="utf-8"))
    return document


def validate_error_config(config: Mapping[str, Any]) -> None:
    """Refuse a configuration that would silently change what the study measures."""

    severities = list(config.get("severities", []))
    if tuple(severities) != SEVERITIES:
        raise ValueError(f"severities must be exactly {SEVERITIES}, got {tuple(severities)}")

    channels = config.get("channels", {})
    unknown = sorted(set(channels) - set(ERROR_CHANNELS))
    if unknown:
        raise ValueError(f"unknown error channels in configuration: {unknown}")
    missing = sorted(set(ERROR_CHANNELS) - set(channels))
    if missing:
        raise ValueError(f"missing error channels in configuration: {missing}")

    for channel, parameters in channels.items():
        expected = CHANNEL_PARAMETERS[channel]
        unknown_parameters = sorted(set(parameters) - set(expected))
        if unknown_parameters:
            raise ValueError(f"channel {channel!r} has unknown parameters: {unknown_parameters}")
        missing_parameters = sorted(set(expected) - set(parameters))
        if missing_parameters:
            raise ValueError(f"channel {channel!r} is missing parameters: {missing_parameters}")

        for name, values in parameters.items():
            if len(values) != len(SEVERITIES):
                raise ValueError(
                    f"{name} must give one value per severity ({len(SEVERITIES)}), "
                    f"got {len(values)}"
                )
            for value in values:
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"{name} values must be numbers")
                if not np.isfinite(value) or value < 0.0:
                    raise ValueError(f"{name} values must be finite and non-negative")
            if values[0] != 0.0:
                raise ValueError(
                    f"{name} at severity zero must be exactly 0; zero is the reference "
                    "every other severity is measured against"
                )


def parameter(
    channel: str, name: str, severity: str, config: Optional[Mapping[str, Any]] = None
) -> float:
    """Return one channel parameter at one severity."""

    document = load_error_config() if config is None else config
    return float(document["channels"][channel][name][SEVERITIES.index(severity)])


def configuration_id(channel: str, severity: str) -> str:
    """Name one of the study's own configurations, the same way every time."""

    if channel not in ERROR_CHANNELS:
        raise ValueError(f"unknown channel: {channel!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity: {severity!r}")
    return f"{channel}-{severity}"


def imported_configuration_id(source: str) -> str:
    """Name a configuration built from a `bev-calibration-lab` artifact."""

    if not source:
        raise ValueError("an imported configuration must name its source")
    return f"{IMPORTED_PREFIX}{source}"


def apply_error_pipeline(
    history: Sequence[WorldFrame],
    current_index: int,
    config: ErrorConfiguration,
    key: ErrorKey,
    *,
    stages: Optional[ChannelStages] = None,
) -> tuple[TrackState, ...]:
    """Corrupt one observation, applying every channel in the fixed order."""

    if not history:
        raise ValueError("history must contain at least one frame")
    if current_index < 0 or current_index >= len(history):
        raise IndexError(f"current_index {current_index} is outside the history")

    missing = sorted(set(ERROR_CHANNELS) - set(config.severity_by_channel))
    if missing:
        raise ValueError(f"configuration does not name a severity for every channel: {missing}")

    applied = ChannelStages() if stages is None else stages
    frame = history[current_index]
    context: dict[str, Any] = {
        "history": tuple(history),
        "current_index": current_index,
        "config": config,
        "key": key,
    }

    tracks: tuple[TrackState, ...] = frame.tracks
    tracks = applied.latency(tracks, **context)
    tracks = applied.visibility(tracks, **context)
    tracks = applied.geometry(tracks, **context)
    tracks = applied.tracking(tracks, **context)
    return tracks
