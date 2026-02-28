"""Core data models for glasses_radar.

This module defines the dataclasses that serve as shared data contracts
across all modules in the glasses_radar package:

- ``BLEDeviceSnapshot``: Captures a point-in-time snapshot of a BLE advertisement.
- ``Fingerprint``: Represents a known smart glasses BLE signature from the database.
- ``DetectionEvent``: Records a confirmed match between a BLE device and a fingerprint.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BLEDeviceSnapshot:
    """A point-in-time snapshot of data captured from a single BLE advertisement.

    Attributes:
        address: The BLE device MAC address (e.g. ``"AA:BB:CC:DD:EE:FF"``).
        name: The advertised local name of the device, or ``None`` if not present.
        rssi: Received Signal Strength Indicator in dBm (negative integer).
        manufacturer_data: Mapping of manufacturer ID (int) to raw payload bytes.
        service_uuids: List of advertised service UUID strings (normalised to
            lowercase 128-bit canonical form where possible).
        service_data: Mapping of service UUID string to raw payload bytes.
        tx_power: Advertised TX power level in dBm, or ``None`` if not present.
        timestamp: UTC datetime at which the advertisement was received.
        raw_advertisement: Optional dict holding any additional advertisement
            fields for future extensibility.
    """

    address: str
    name: str | None
    rssi: int
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_uuids: list[str] = field(default_factory=list)
    service_data: dict[str, bytes] = field(default_factory=dict)
    tx_power: int | None = None
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc)
    )
    raw_advertisement: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate and normalise fields after construction."""
        if not isinstance(self.address, str) or not self.address:
            raise ValueError("BLEDeviceSnapshot.address must be a non-empty string")
        # Normalise address to uppercase for consistent comparison
        self.address = self.address.upper()
        # Normalise service UUIDs to lowercase
        self.service_uuids = [uuid.lower() for uuid in self.service_uuids]
        # Normalise service_data keys to lowercase
        self.service_data = {k.lower(): v for k, v in self.service_data.items()}

    @property
    def manufacturer_ids(self) -> list[int]:
        """Return the list of manufacturer IDs present in this advertisement."""
        return list(self.manufacturer_data.keys())

    def to_dict(self) -> dict[str, Any]:
        """Serialise the snapshot to a JSON-compatible dictionary.

        Returns:
            A dictionary with all snapshot fields serialised to basic Python
            types.  Bytes values are hex-encoded strings.
        """
        return {
            "address": self.address,
            "name": self.name,
            "rssi": self.rssi,
            "manufacturer_data": {
                str(k): v.hex() for k, v in self.manufacturer_data.items()
            },
            "service_uuids": self.service_uuids,
            "service_data": {k: v.hex() for k, v in self.service_data.items()},
            "tx_power": self.tx_power,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class ConfidenceWeights:
    """Point weights awarded for each type of fingerprint field match.

    Attributes:
        manufacturer_id: Points awarded when the advertised manufacturer ID
            matches one of the fingerprint's known manufacturer IDs.
        service_uuid: Points awarded when an advertised service UUID matches
            one of the fingerprint's known service UUIDs.
        name_pattern: Points awarded when the device local name contains one
            of the fingerprint's known name patterns.
    """

    manufacturer_id: int = 50
    service_uuid: int = 30
    name_pattern: int = 40

    def __post_init__(self) -> None:
        """Validate that all weights are non-negative integers."""
        for attr in ("manufacturer_id", "service_uuid", "name_pattern"):
            val = getattr(self, attr)
            if not isinstance(val, int) or val < 0:
                raise ValueError(
                    f"ConfidenceWeights.{attr} must be a non-negative integer, got {val!r}"
                )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConfidenceWeights":
        """Construct a ``ConfidenceWeights`` instance from a dictionary.

        Args:
            data: Dictionary with optional keys ``manufacturer_id``,
                ``service_uuid``, and ``name_pattern``.

        Returns:
            A populated ``ConfidenceWeights`` instance.

        Raises:
            ValueError: If any provided value is not a non-negative integer.
        """
        return cls(
            manufacturer_id=int(data.get("manufacturer_id", 50)),
            service_uuid=int(data.get("service_uuid", 30)),
            name_pattern=int(data.get("name_pattern", 40)),
        )

    def to_dict(self) -> dict[str, int]:
        """Serialise to a plain dictionary."""
        return {
            "manufacturer_id": self.manufacturer_id,
            "service_uuid": self.service_uuid,
            "name_pattern": self.name_pattern,
        }


@dataclass
class Fingerprint:
    """A known smart glasses BLE fingerprint loaded from the fingerprint database.

    Each ``Fingerprint`` describes the BLE advertisement characteristics of a
    specific smart glasses model.  The matcher uses these attributes to compute
    a confidence score for incoming ``BLEDeviceSnapshot`` objects.

    Attributes:
        id: Unique identifier string for this fingerprint (e.g.
            ``"meta-ray-ban-gen2"``).
        name: Human-readable device name.
        vendor: Manufacturer or vendor name.
        notes: Optional free-text notes about the fingerprint signature.
        enabled: Whether this fingerprint is active for matching.
        manufacturer_ids: List of BLE manufacturer IDs (16-bit integers) known
            to be advertised by this device.
        service_uuids: List of 128-bit service UUID strings (lowercase) known
            to be advertised by this device.
        name_patterns: List of substrings that may appear in the device's
            advertised local name.
        rssi_threshold: Device-specific RSSI threshold override (dBm).  A
            device is considered "in proximity" when its measured RSSI is
            greater than or equal to this value.
        confidence_weights: Per-field confidence point weights.
        match_threshold: Minimum accumulated confidence score required to raise
            a detection alert.
    """

    id: str
    name: str
    vendor: str
    manufacturer_ids: list[int]
    service_uuids: list[str]
    name_patterns: list[str]
    rssi_threshold: int
    confidence_weights: ConfidenceWeights
    match_threshold: int
    notes: str = ""
    enabled: bool = True

    def __post_init__(self) -> None:
        """Validate required fields and normalise UUID casing."""
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Fingerprint.id must be a non-empty string")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("Fingerprint.name must be a non-empty string")
        if not isinstance(self.vendor, str) or not self.vendor:
            raise ValueError("Fingerprint.vendor must be a non-empty string")
        if not isinstance(self.rssi_threshold, int):
            raise ValueError("Fingerprint.rssi_threshold must be an integer")
        if not isinstance(self.match_threshold, int) or self.match_threshold < 0:
            raise ValueError(
                "Fingerprint.match_threshold must be a non-negative integer"
            )
        # Normalise service UUIDs to lowercase for consistent comparison
        self.service_uuids = [uuid.lower() for uuid in self.service_uuids]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Fingerprint":
        """Construct a ``Fingerprint`` from a raw dictionary (e.g. parsed JSON).

        Args:
            data: Dictionary matching the fingerprint JSON schema.

        Returns:
            A fully populated ``Fingerprint`` instance.

        Raises:
            KeyError: If a required field is missing from *data*.
            ValueError: If a field value fails validation.
        """
        required_keys = (
            "id",
            "name",
            "vendor",
            "manufacturer_ids",
            "service_uuids",
            "name_patterns",
            "rssi_threshold",
            "confidence_weights",
            "match_threshold",
        )
        for key in required_keys:
            if key not in data:
                raise KeyError(
                    f"Required fingerprint field '{key}' is missing from data: {data!r}"
                )

        weights = ConfidenceWeights.from_dict(data["confidence_weights"])
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            vendor=str(data["vendor"]),
            notes=str(data.get("notes", "")),
            enabled=bool(data.get("enabled", True)),
            manufacturer_ids=[int(mid) for mid in data["manufacturer_ids"]],
            service_uuids=[str(uuid) for uuid in data["service_uuids"]],
            name_patterns=[str(p) for p in data["name_patterns"]],
            rssi_threshold=int(data["rssi_threshold"]),
            confidence_weights=weights,
            match_threshold=int(data["match_threshold"]),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise the fingerprint to a JSON-compatible dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "notes": self.notes,
            "enabled": self.enabled,
            "manufacturer_ids": self.manufacturer_ids,
            "service_uuids": self.service_uuids,
            "name_patterns": self.name_patterns,
            "rssi_threshold": self.rssi_threshold,
            "confidence_weights": self.confidence_weights.to_dict(),
            "match_threshold": self.match_threshold,
        }

    @property
    def max_possible_score(self) -> int:
        """Compute the maximum confidence score achievable for this fingerprint.

        The maximum score is the sum of all confidence weights, regardless of
        whether the fingerprint has entries for each field.

        Returns:
            Integer representing the highest possible confidence score.
        """
        w = self.confidence_weights
        return w.manufacturer_id + w.service_uuid + w.name_pattern


@dataclass
class DetectionEvent:
    """Records a confirmed match between a BLE device snapshot and a fingerprint.

    A ``DetectionEvent`` is created by the matcher when a ``BLEDeviceSnapshot``
    accumulates a confidence score at or above the fingerprint's
    ``match_threshold``.

    Attributes:
        device: The ``BLEDeviceSnapshot`` that triggered the detection.
        fingerprint: The ``Fingerprint`` that was matched.
        confidence: The accumulated confidence score (0–max_possible_score).
        matched_fields: List of field names that contributed to the match
            (e.g. ``["manufacturer_id", "name_pattern"]``).
        timestamp: UTC datetime when the detection event was created.
        alerted: Whether an alert notification has already been issued for this
            event (used to implement per-device cooldown).
    """

    device: BLEDeviceSnapshot
    fingerprint: Fingerprint
    confidence: int
    matched_fields: list[str]
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(tz=datetime.timezone.utc)
    )
    alerted: bool = False

    def __post_init__(self) -> None:
        """Validate confidence score and matched_fields."""
        if not isinstance(self.confidence, int) or self.confidence < 0:
            raise ValueError(
                "DetectionEvent.confidence must be a non-negative integer"
            )
        if not isinstance(self.matched_fields, list):
            raise ValueError("DetectionEvent.matched_fields must be a list")

    @property
    def confidence_percent(self) -> float:
        """Return confidence as a percentage of the maximum possible score.

        Returns:
            Float in the range 0.0–100.0, or 0.0 if the max score is zero.
        """
        max_score = self.fingerprint.max_possible_score
        if max_score == 0:
            return 0.0
        return round(min(self.confidence / max_score * 100.0, 100.0), 1)

    @property
    def is_in_proximity(self) -> bool:
        """Return ``True`` if the device RSSI is within the fingerprint threshold.

        A device is considered "in proximity" when its RSSI is greater than or
        equal to (i.e. stronger than) the fingerprint's ``rssi_threshold``.
        """
        return self.device.rssi >= self.fingerprint.rssi_threshold

    def to_dict(self) -> dict[str, Any]:
        """Serialise the detection event to a JSON-compatible dictionary.

        This format is used for structured log output (``--log`` flag).

        Returns:
            Dictionary suitable for ``json.dumps``.
        """
        return {
            "timestamp": self.timestamp.isoformat(),
            "address": self.device.address,
            "name": self.device.name,
            "fingerprint_id": self.fingerprint.id,
            "fingerprint_name": self.fingerprint.name,
            "vendor": self.fingerprint.vendor,
            "rssi": self.device.rssi,
            "rssi_threshold": self.fingerprint.rssi_threshold,
            "confidence": self.confidence,
            "confidence_percent": self.confidence_percent,
            "matched_fields": self.matched_fields,
            "in_proximity": self.is_in_proximity,
        }
