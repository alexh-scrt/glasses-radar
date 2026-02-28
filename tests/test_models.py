"""Unit tests for glasses_radar.models.

Covers BLEDeviceSnapshot, ConfidenceWeights, Fingerprint, and DetectionEvent
dataclasses including construction, validation, serialisation, and computed
properties.
"""

from __future__ import annotations

import datetime

import pytest

from glasses_radar.models import (
    BLEDeviceSnapshot,
    ConfidenceWeights,
    DetectionEvent,
    Fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_snapshot(
    address: str = "aa:bb:cc:dd:ee:ff",
    name: str | None = "RayBan-1234",
    rssi: int = -62,
    manufacturer_data: dict | None = None,
    service_uuids: list[str] | None = None,
) -> BLEDeviceSnapshot:
    """Return a minimal BLEDeviceSnapshot for testing."""
    return BLEDeviceSnapshot(
        address=address,
        name=name,
        rssi=rssi,
        manufacturer_data=manufacturer_data or {1177: b"\x00\x01"},
        service_uuids=service_uuids or ["0000fe59-0000-1000-8000-00805f9b34fb"],
    )


def make_fingerprint(
    id: str = "meta-ray-ban-gen2",
    rssi_threshold: int = -70,
    match_threshold: int = 40,
) -> Fingerprint:
    """Return a minimal Fingerprint for testing."""
    return Fingerprint(
        id=id,
        name="Meta Ray-Ban Smart Glasses (Gen 2)",
        vendor="Meta / EssilorLuxottica",
        manufacturer_ids=[1177],
        service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
        name_patterns=["Ray-Ban", "RayBan"],
        rssi_threshold=rssi_threshold,
        confidence_weights=ConfidenceWeights(manufacturer_id=50, service_uuid=30, name_pattern=40),
        match_threshold=match_threshold,
    )


def make_detection_event(
    confidence: int = 80,
    matched_fields: list[str] | None = None,
) -> DetectionEvent:
    """Return a DetectionEvent for testing."""
    return DetectionEvent(
        device=make_snapshot(),
        fingerprint=make_fingerprint(),
        confidence=confidence,
        matched_fields=matched_fields or ["manufacturer_id", "name_pattern"],
    )


# ---------------------------------------------------------------------------
# BLEDeviceSnapshot tests
# ---------------------------------------------------------------------------

class TestBLEDeviceSnapshot:
    """Tests for BLEDeviceSnapshot."""

    def test_address_normalised_to_uppercase(self) -> None:
        snap = make_snapshot(address="aa:bb:cc:dd:ee:ff")
        assert snap.address == "AA:BB:CC:DD:EE:FF"

    def test_service_uuids_normalised_to_lowercase(self) -> None:
        snap = BLEDeviceSnapshot(
            address="AA:BB:CC:DD:EE:FF",
            name=None,
            rssi=-70,
            service_uuids=["0000FE59-0000-1000-8000-00805F9B34FB"],
        )
        assert snap.service_uuids == ["0000fe59-0000-1000-8000-00805f9b34fb"]

    def test_service_data_keys_normalised_to_lowercase(self) -> None:
        snap = BLEDeviceSnapshot(
            address="AA:BB:CC:DD:EE:FF",
            name=None,
            rssi=-70,
            service_data={"0000FE59-0000-1000-8000-00805F9B34FB": b"\x01"},
        )
        assert "0000fe59-0000-1000-8000-00805f9b34fb" in snap.service_data

    def test_empty_address_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="address"):
            BLEDeviceSnapshot(address="", name=None, rssi=-70)

    def test_manufacturer_ids_property(self) -> None:
        snap = make_snapshot(manufacturer_data={1177: b"\x00", 756: b"\x01"})
        assert set(snap.manufacturer_ids) == {1177, 756}

    def test_manufacturer_ids_empty_when_no_data(self) -> None:
        snap = BLEDeviceSnapshot(address="AA:BB:CC:DD:EE:FF", name=None, rssi=-70)
        assert snap.manufacturer_ids == []

    def test_timestamp_is_utc(self) -> None:
        snap = make_snapshot()
        assert snap.timestamp.tzinfo is datetime.timezone.utc

    def test_name_can_be_none(self) -> None:
        snap = make_snapshot(name=None)
        assert snap.name is None

    def test_to_dict_structure(self) -> None:
        snap = make_snapshot()
        d = snap.to_dict()
        assert d["address"] == snap.address
        assert d["name"] == snap.name
        assert d["rssi"] == snap.rssi
        assert isinstance(d["manufacturer_data"], dict)
        assert isinstance(d["service_uuids"], list)
        assert isinstance(d["timestamp"], str)

    def test_to_dict_manufacturer_data_is_hex_string(self) -> None:
        snap = make_snapshot(manufacturer_data={1177: b"\xde\xad"})
        d = snap.to_dict()
        assert d["manufacturer_data"]["1177"] == "dead"

    def test_default_manufacturer_data_is_empty_dict(self) -> None:
        snap = BLEDeviceSnapshot(address="AA:BB:CC:DD:EE:FF", name=None, rssi=-80)
        assert snap.manufacturer_data == {}

    def test_default_service_uuids_is_empty_list(self) -> None:
        snap = BLEDeviceSnapshot(address="AA:BB:CC:DD:EE:FF", name=None, rssi=-80)
        assert snap.service_uuids == []

    def test_tx_power_defaults_to_none(self) -> None:
        snap = BLEDeviceSnapshot(address="AA:BB:CC:DD:EE:FF", name=None, rssi=-80)
        assert snap.tx_power is None

    def test_tx_power_can_be_set(self) -> None:
        snap = BLEDeviceSnapshot(
            address="AA:BB:CC:DD:EE:FF", name=None, rssi=-80, tx_power=-10
        )
        assert snap.tx_power == -10


# ---------------------------------------------------------------------------
# ConfidenceWeights tests
# ---------------------------------------------------------------------------

class TestConfidenceWeights:
    """Tests for ConfidenceWeights."""

    def test_default_values(self) -> None:
        w = ConfidenceWeights()
        assert w.manufacturer_id == 50
        assert w.service_uuid == 30
        assert w.name_pattern == 40

    def test_custom_values(self) -> None:
        w = ConfidenceWeights(manufacturer_id=60, service_uuid=20, name_pattern=50)
        assert w.manufacturer_id == 60
        assert w.service_uuid == 20
        assert w.name_pattern == 50

    def test_negative_weight_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="manufacturer_id"):
            ConfidenceWeights(manufacturer_id=-1)

    def test_from_dict_full(self) -> None:
        w = ConfidenceWeights.from_dict(
            {"manufacturer_id": 60, "service_uuid": 20, "name_pattern": 50}
        )
        assert w.manufacturer_id == 60
        assert w.service_uuid == 20
        assert w.name_pattern == 50

    def test_from_dict_partial_uses_defaults(self) -> None:
        w = ConfidenceWeights.from_dict({"manufacturer_id": 70})
        assert w.manufacturer_id == 70
        assert w.service_uuid == 30
        assert w.name_pattern == 40

    def test_from_dict_empty_uses_defaults(self) -> None:
        w = ConfidenceWeights.from_dict({})
        assert w == ConfidenceWeights()

    def test_to_dict_round_trip(self) -> None:
        w = ConfidenceWeights(manufacturer_id=55, service_uuid=25, name_pattern=45)
        assert ConfidenceWeights.from_dict(w.to_dict()) == w


# ---------------------------------------------------------------------------
# Fingerprint tests
# ---------------------------------------------------------------------------

class TestFingerprint:
    """Tests for Fingerprint."""

    def test_basic_construction(self) -> None:
        fp = make_fingerprint()
        assert fp.id == "meta-ray-ban-gen2"
        assert fp.enabled is True
        assert fp.notes == ""

    def test_service_uuids_normalised_to_lowercase(self) -> None:
        fp = Fingerprint(
            id="test",
            name="Test Device",
            vendor="Test Vendor",
            manufacturer_ids=[],
            service_uuids=["0000FE59-0000-1000-8000-00805F9B34FB"],
            name_patterns=[],
            rssi_threshold=-70,
            confidence_weights=ConfidenceWeights(),
            match_threshold=40,
        )
        assert fp.service_uuids == ["0000fe59-0000-1000-8000-00805f9b34fb"]

    def test_empty_id_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="id"):
            Fingerprint(
                id="",
                name="Test",
                vendor="Vendor",
                manufacturer_ids=[],
                service_uuids=[],
                name_patterns=[],
                rssi_threshold=-70,
                confidence_weights=ConfidenceWeights(),
                match_threshold=40,
            )

    def test_empty_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="name"):
            Fingerprint(
                id="test",
                name="",
                vendor="Vendor",
                manufacturer_ids=[],
                service_uuids=[],
                name_patterns=[],
                rssi_threshold=-70,
                confidence_weights=ConfidenceWeights(),
                match_threshold=40,
            )

    def test_empty_vendor_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="vendor"):
            Fingerprint(
                id="test",
                name="Test",
                vendor="",
                manufacturer_ids=[],
                service_uuids=[],
                name_patterns=[],
                rssi_threshold=-70,
                confidence_weights=ConfidenceWeights(),
                match_threshold=40,
            )

    def test_negative_match_threshold_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="match_threshold"):
            Fingerprint(
                id="test",
                name="Test",
                vendor="Vendor",
                manufacturer_ids=[],
                service_uuids=[],
                name_patterns=[],
                rssi_threshold=-70,
                confidence_weights=ConfidenceWeights(),
                match_threshold=-1,
            )

    def test_max_possible_score(self) -> None:
        fp = make_fingerprint()
        # 50 + 30 + 40 = 120
        assert fp.max_possible_score == 120

    def test_max_possible_score_zero_weights(self) -> None:
        fp = Fingerprint(
            id="test",
            name="Test",
            vendor="Vendor",
            manufacturer_ids=[],
            service_uuids=[],
            name_patterns=[],
            rssi_threshold=-70,
            confidence_weights=ConfidenceWeights(
                manufacturer_id=0, service_uuid=0, name_pattern=0
            ),
            match_threshold=0,
        )
        assert fp.max_possible_score == 0

    def test_from_dict_full(self) -> None:
        data = {
            "id": "bose-frames-alto",
            "name": "Bose Frames Alto",
            "vendor": "Bose Corporation",
            "notes": "Bose Frames Alto audio sunglasses.",
            "enabled": True,
            "manufacturer_ids": [2291],
            "service_uuids": ["0000febe-0000-1000-8000-00805f9b34fb"],
            "name_patterns": ["Bose Frames", "Bose Alto"],
            "rssi_threshold": -70,
            "confidence_weights": {
                "manufacturer_id": 50,
                "service_uuid": 30,
                "name_pattern": 40,
            },
            "match_threshold": 40,
        }
        fp = Fingerprint.from_dict(data)
        assert fp.id == "bose-frames-alto"
        assert fp.name == "Bose Frames Alto"
        assert fp.vendor == "Bose Corporation"
        assert fp.notes == "Bose Frames Alto audio sunglasses."
        assert fp.enabled is True
        assert fp.manufacturer_ids == [2291]
        assert fp.service_uuids == ["0000febe-0000-1000-8000-00805f9b34fb"]
        assert fp.name_patterns == ["Bose Frames", "Bose Alto"]
        assert fp.rssi_threshold == -70
        assert fp.match_threshold == 40

    def test_from_dict_missing_required_field_raises_key_error(self) -> None:
        data = {
            "id": "test",
            "name": "Test",
            # missing 'vendor' and other required fields
        }
        with pytest.raises(KeyError):
            Fingerprint.from_dict(data)

    def test_from_dict_enabled_defaults_true(self) -> None:
        data = {
            "id": "test",
            "name": "Test",
            "vendor": "Vendor",
            "manufacturer_ids": [],
            "service_uuids": [],
            "name_patterns": [],
            "rssi_threshold": -70,
            "confidence_weights": {"manufacturer_id": 50, "service_uuid": 30, "name_pattern": 40},
            "match_threshold": 40,
        }
        fp = Fingerprint.from_dict(data)
        assert fp.enabled is True
        assert fp.notes == ""

    def test_to_dict_round_trip(self) -> None:
        fp = make_fingerprint()
        d = fp.to_dict()
        fp2 = Fingerprint.from_dict(d)
        assert fp2.id == fp.id
        assert fp2.name == fp.name
        assert fp2.vendor == fp.vendor
        assert fp2.manufacturer_ids == fp.manufacturer_ids
        assert fp2.service_uuids == fp.service_uuids
        assert fp2.name_patterns == fp.name_patterns
        assert fp2.rssi_threshold == fp.rssi_threshold
        assert fp2.match_threshold == fp.match_threshold


# ---------------------------------------------------------------------------
# DetectionEvent tests
# ---------------------------------------------------------------------------

class TestDetectionEvent:
    """Tests for DetectionEvent."""

    def test_basic_construction(self) -> None:
        event = make_detection_event(confidence=80)
        assert event.confidence == 80
        assert event.matched_fields == ["manufacturer_id", "name_pattern"]
        assert event.alerted is False

    def test_negative_confidence_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            make_detection_event(confidence=-1)

    def test_matched_fields_not_list_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="matched_fields"):
            DetectionEvent(
                device=make_snapshot(),
                fingerprint=make_fingerprint(),
                confidence=50,
                matched_fields="manufacturer_id",  # type: ignore[arg-type]
            )

    def test_confidence_percent_full_match(self) -> None:
        # max_possible_score = 50 + 30 + 40 = 120
        event = make_detection_event(confidence=120)
        assert event.confidence_percent == 100.0

    def test_confidence_percent_partial(self) -> None:
        # 60 / 120 = 50.0
        event = make_detection_event(confidence=60)
        assert event.confidence_percent == 50.0

    def test_confidence_percent_zero_max_score(self) -> None:
        fp = Fingerprint(
            id="test",
            name="Test",
            vendor="Vendor",
            manufacturer_ids=[],
            service_uuids=[],
            name_patterns=[],
            rssi_threshold=-70,
            confidence_weights=ConfidenceWeights(
                manufacturer_id=0, service_uuid=0, name_pattern=0
            ),
            match_threshold=0,
        )
        event = DetectionEvent(
            device=make_snapshot(),
            fingerprint=fp,
            confidence=0,
            matched_fields=[],
        )
        assert event.confidence_percent == 0.0

    def test_confidence_percent_capped_at_100(self) -> None:
        # Confidence somehow exceeds max; should be capped.
        event = make_detection_event(confidence=999)
        assert event.confidence_percent == 100.0

    def test_is_in_proximity_true_when_rssi_above_threshold(self) -> None:
        # device rssi = -62, fingerprint threshold = -70 => -62 >= -70 => True
        event = make_detection_event()
        assert event.device.rssi == -62
        assert event.fingerprint.rssi_threshold == -70
        assert event.is_in_proximity is True

    def test_is_in_proximity_false_when_rssi_below_threshold(self) -> None:
        snap = make_snapshot(rssi=-80)
        fp = make_fingerprint(rssi_threshold=-70)
        event = DetectionEvent(
            device=snap,
            fingerprint=fp,
            confidence=50,
            matched_fields=["name_pattern"],
        )
        # -80 >= -70 => False
        assert event.is_in_proximity is False

    def test_is_in_proximity_true_at_exact_threshold(self) -> None:
        snap = make_snapshot(rssi=-70)
        fp = make_fingerprint(rssi_threshold=-70)
        event = DetectionEvent(
            device=snap,
            fingerprint=fp,
            confidence=50,
            matched_fields=["name_pattern"],
        )
        assert event.is_in_proximity is True

    def test_timestamp_is_utc(self) -> None:
        event = make_detection_event()
        assert event.timestamp.tzinfo is datetime.timezone.utc

    def test_to_dict_structure(self) -> None:
        event = make_detection_event(confidence=80)
        d = event.to_dict()
        assert d["address"] == event.device.address
        assert d["fingerprint_id"] == event.fingerprint.id
        assert d["fingerprint_name"] == event.fingerprint.name
        assert d["vendor"] == event.fingerprint.vendor
        assert d["rssi"] == event.device.rssi
        assert d["rssi_threshold"] == event.fingerprint.rssi_threshold
        assert d["confidence"] == 80
        assert isinstance(d["confidence_percent"], float)
        assert isinstance(d["matched_fields"], list)
        assert isinstance(d["in_proximity"], bool)
        assert isinstance(d["timestamp"], str)

    def test_to_dict_matched_fields(self) -> None:
        event = make_detection_event(
            matched_fields=["manufacturer_id", "service_uuid", "name_pattern"]
        )
        d = event.to_dict()
        assert d["matched_fields"] == [
            "manufacturer_id",
            "service_uuid",
            "name_pattern",
        ]

    def test_alerted_flag_defaults_false(self) -> None:
        event = make_detection_event()
        assert event.alerted is False

    def test_alerted_flag_can_be_set(self) -> None:
        event = make_detection_event()
        event.alerted = True
        assert event.alerted is True
