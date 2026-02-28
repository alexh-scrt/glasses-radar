"""Unit tests for glasses_radar.matcher.

Covers FingerprintMatcher including confidence scoring, match threshold
behaviour, multi-field matching, deduplication, ordering, and the global
RSSI threshold override.
"""

from __future__ import annotations

import pytest

from glasses_radar.fingerprints import FingerprintDatabase
from glasses_radar.matcher import FingerprintMatcher
from glasses_radar.models import (
    BLEDeviceSnapshot,
    ConfidenceWeights,
    DetectionEvent,
    Fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_fingerprint(
    fp_id: str = "test-fp",
    manufacturer_ids: list[int] | None = None,
    service_uuids: list[str] | None = None,
    name_patterns: list[str] | None = None,
    rssi_threshold: int = -70,
    match_threshold: int = 40,
    weights: ConfidenceWeights | None = None,
    enabled: bool = True,
) -> Fingerprint:
    """Build a Fingerprint for testing."""
    return Fingerprint(
        id=fp_id,
        name=f"Test Device ({fp_id})",
        vendor="Test Vendor",
        manufacturer_ids=manufacturer_ids if manufacturer_ids is not None else [1177],
        service_uuids=service_uuids if service_uuids is not None else [
            "0000fe59-0000-1000-8000-00805f9b34fb"
        ],
        name_patterns=name_patterns if name_patterns is not None else ["RayBan", "Ray-Ban"],
        rssi_threshold=rssi_threshold,
        confidence_weights=weights or ConfidenceWeights(
            manufacturer_id=50, service_uuid=30, name_pattern=40
        ),
        match_threshold=match_threshold,
        enabled=enabled,
    )


def make_snapshot(
    address: str = "AA:BB:CC:DD:EE:FF",
    name: str | None = "Ray-Ban-1234",
    rssi: int = -65,
    manufacturer_data: dict | None = None,
    service_uuids: list[str] | None = None,
) -> BLEDeviceSnapshot:
    """Build a BLEDeviceSnapshot for testing."""
    return BLEDeviceSnapshot(
        address=address,
        name=name,
        rssi=rssi,
        manufacturer_data=manufacturer_data
        if manufacturer_data is not None
        else {1177: b"\x00\x01"},
        service_uuids=service_uuids
        if service_uuids is not None
        else ["0000fe59-0000-1000-8000-00805f9b34fb"],
    )


def make_db_with(*fingerprints: Fingerprint) -> FingerprintDatabase:
    """Construct a FingerprintDatabase directly from Fingerprint objects."""
    return FingerprintDatabase(
        version="1.0.0",
        description="Test DB",
        last_updated="2024-01-01",
        fingerprints=list(fingerprints),
    )


# ---------------------------------------------------------------------------
# FingerprintMatcher.score_against
# ---------------------------------------------------------------------------

class TestScoreAgainst:
    """Tests for the low-level score_against method."""

    def setup_method(self) -> None:
        self.fp = make_fingerprint()
        self.db = make_db_with(self.fp)
        self.matcher = FingerprintMatcher(self.db)

    def test_all_fields_match_returns_full_score(self) -> None:
        snapshot = make_snapshot(
            name="Ray-Ban-ABCD",
            manufacturer_data={1177: b"\x00"},
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
        )
        score, fields = self.matcher.score_against(snapshot, self.fp)
        # 50 (mfr) + 30 (uuid) + 40 (name) = 120
        assert score == 120
        assert set(fields) == {"manufacturer_id", "service_uuid", "name_pattern"}

    def test_manufacturer_id_only_match(self) -> None:
        snapshot = make_snapshot(
            name="SomeUnknownDevice",
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
        )
        score, fields = self.matcher.score_against(snapshot, self.fp)
        assert score == 50
        assert fields == ["manufacturer_id"]

    def test_service_uuid_only_match(self) -> None:
        snapshot = make_snapshot(
            name="SomeUnknownDevice",
            manufacturer_data={},
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
        )
        score, fields = self.matcher.score_against(snapshot, self.fp)
        assert score == 30
        assert fields == ["service_uuid"]

    def test_name_pattern_only_match(self) -> None:
        snapshot = make_snapshot(
            name="Ray-Ban-ABCD",
            manufacturer_data={},
            service_uuids=[],
        )
        score, fields = self.matcher.score_against(snapshot, self.fp)
        assert score == 40
        assert fields == ["name_pattern"]

    def test_no_fields_match_returns_zero(self) -> None:
        snapshot = make_snapshot(
            name="SomeOtherDevice",
            manufacturer_data={9999: b"\x00"},
            service_uuids=["00000000-0000-0000-0000-000000000000"],
        )
        score, fields = self.matcher.score_against(snapshot, self.fp)
        assert score == 0
        assert fields == []

    def test_name_pattern_matched_only_once_even_if_multiple_patterns_present(self) -> None:
        """If two name patterns match, the weight is awarded only once."""
        fp = make_fingerprint(
            name_patterns=["Ray-Ban", "RayBan"],
            weights=ConfidenceWeights(manufacturer_id=0, service_uuid=0, name_pattern=40),
        )
        snapshot = make_snapshot(
            name="Ray-Ban RayBan Special Edition",
            manufacturer_data={},
            service_uuids=[],
        )
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 40
        assert fields.count("name_pattern") == 1

    def test_manufacturer_id_case_no_overlap(self) -> None:
        fp = make_fingerprint(manufacturer_ids=[1111])
        snapshot = make_snapshot(manufacturer_data={2222: b"\x00"})
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 0
        assert "manufacturer_id" not in fields

    def test_service_uuid_match_is_case_insensitive(self) -> None:
        """UUIDs in the snapshot are already lowercased by the model, but the
        fingerprint lookup should also handle uppercase patterns."""
        fp = make_fingerprint(
            service_uuids=["0000FE59-0000-1000-8000-00805F9B34FB"],
            manufacturer_ids=[],
            name_patterns=[],
        )
        snapshot = make_snapshot(
            manufacturer_data={},
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
            name=None,
        )
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 30
        assert "service_uuid" in fields

    def test_name_pattern_match_is_case_insensitive(self) -> None:
        fp = make_fingerprint(name_patterns=["RayBan"])
        snapshot = make_snapshot(name="RAYBAN-SPECIAL", manufacturer_data={}, service_uuids=[])
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 40
        assert "name_pattern" in fields

    def test_no_match_when_snapshot_name_is_none(self) -> None:
        fp = make_fingerprint(name_patterns=["RayBan"])
        snapshot = make_snapshot(name=None, manufacturer_data={}, service_uuids=[])
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 0
        assert "name_pattern" not in fields

    def test_fingerprint_with_empty_manufacturer_ids_skips_mid_check(self) -> None:
        fp = make_fingerprint(manufacturer_ids=[])
        snapshot = make_snapshot(manufacturer_data={1177: b"\x00"}, service_uuids=[])
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert "manufacturer_id" not in fields

    def test_fingerprint_with_empty_service_uuids_skips_uuid_check(self) -> None:
        fp = make_fingerprint(service_uuids=[])
        snapshot = make_snapshot(
            manufacturer_data={}, service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"]
        )
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert "service_uuid" not in fields

    def test_custom_weights_applied_correctly(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            weights=ConfidenceWeights(manufacturer_id=100, service_uuid=0, name_pattern=0),
        )
        snapshot = make_snapshot(
            manufacturer_data={1177: b"\x00"}, service_uuids=[], name=None
        )
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 100
        assert fields == ["manufacturer_id"]

    def test_multiple_matching_manufacturer_ids_score_once(self) -> None:
        """Even if the fingerprint has multiple IDs and the device matches several,
        the weight is only awarded once."""
        fp = make_fingerprint(manufacturer_ids=[1177, 756])
        snapshot = make_snapshot(
            manufacturer_data={1177: b"\x00", 756: b"\x01"}, service_uuids=[], name=None
        )
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 50  # awarded once only
        assert fields.count("manufacturer_id") == 1

    def test_multiple_matching_service_uuids_score_once(self) -> None:
        fp = make_fingerprint(
            service_uuids=[
                "0000fe59-0000-1000-8000-00805f9b34fb",
                "0000180a-0000-1000-8000-00805f9b34fb",
            ],
            manufacturer_ids=[],
            name_patterns=[],
        )
        snapshot = make_snapshot(
            manufacturer_data={},
            service_uuids=[
                "0000fe59-0000-1000-8000-00805f9b34fb",
                "0000180a-0000-1000-8000-00805f9b34fb",
            ],
            name=None,
        )
        matcher = FingerprintMatcher(make_db_with(fp))
        score, fields = matcher.score_against(snapshot, fp)
        assert score == 30
        assert fields.count("service_uuid") == 1


# ---------------------------------------------------------------------------
# FingerprintMatcher.match — threshold and event creation
# ---------------------------------------------------------------------------

class TestMatchThreshold:
    """Tests for match() threshold behaviour."""

    def test_returns_event_when_score_meets_threshold(self) -> None:
        fp = make_fingerprint(match_threshold=40)
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        # manufacturer_id only = 50 >= 40
        snapshot = make_snapshot(
            name="UnknownDevice",
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
        )
        events = matcher.match(snapshot)
        assert len(events) == 1
        assert events[0].fingerprint.id == "test-fp"
        assert events[0].confidence == 50

    def test_returns_empty_when_score_below_threshold(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=60,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            name=None,
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
        )
        events = matcher.match(snapshot)
        assert events == []

    def test_returns_empty_when_no_candidates(self) -> None:
        fp = make_fingerprint(manufacturer_ids=[9999], service_uuids=[], name_patterns=[])
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            name="SomeRandomDevice",
            manufacturer_data={1234: b"\x00"},
            service_uuids=["00000000-0000-0000-0000-000000000000"],
        )
        events = matcher.match(snapshot)
        assert events == []

    def test_exact_threshold_score_matches(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=50,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            name=None,
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
        )
        events = matcher.match(snapshot)
        assert len(events) == 1
        assert events[0].confidence == 50

    def test_one_above_threshold_score_matches(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=49,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            name=None,
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
        )
        events = matcher.match(snapshot)
        assert len(events) == 1


# ---------------------------------------------------------------------------
# FingerprintMatcher.match — disabled fingerprints
# ---------------------------------------------------------------------------

class TestMatchDisabledFingerprints:
    """Tests that disabled fingerprints are excluded from matching."""

    def test_disabled_fingerprint_not_matched(self) -> None:
        fp = make_fingerprint(enabled=False)
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot()
        events = matcher.match(snapshot)
        assert events == []

    def test_enabled_fingerprint_matched_alongside_disabled(self) -> None:
        fp_enabled = make_fingerprint(fp_id="enabled-fp", enabled=True)
        fp_disabled = make_fingerprint(
            fp_id="disabled-fp",
            manufacturer_ids=[1177],
            enabled=False,
        )
        db = make_db_with(fp_enabled, fp_disabled)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot()
        events = matcher.match(snapshot)
        assert len(events) == 1
        assert events[0].fingerprint.id == "enabled-fp"


# ---------------------------------------------------------------------------
# FingerprintMatcher.match — multiple fingerprint results
# ---------------------------------------------------------------------------

class TestMatchMultipleFingerprints:
    """Tests for scenarios where multiple fingerprints may match."""

    def test_multiple_fingerprints_both_matched(self) -> None:
        fp1 = make_fingerprint(
            fp_id="fp-1",
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=40,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        fp2 = make_fingerprint(
            fp_id="fp-2",
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=40,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        db = make_db_with(fp1, fp2)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(manufacturer_data={1177: b"\x00"}, service_uuids=[], name=None)
        events = matcher.match(snapshot)
        assert len(events) == 2
        ids = {e.fingerprint.id for e in events}
        assert ids == {"fp-1", "fp-2"}

    def test_results_ordered_by_descending_confidence(self) -> None:
        fp_high = make_fingerprint(
            fp_id="high-score",
            manufacturer_ids=[1177],
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
            name_patterns=["Ray-Ban"],
            match_threshold=1,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=30, name_pattern=40),
        )
        fp_low = make_fingerprint(
            fp_id="low-score",
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=1,
            weights=ConfidenceWeights(manufacturer_id=10, service_uuid=0, name_pattern=0),
        )
        db = make_db_with(fp_high, fp_low)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            manufacturer_data={1177: b"\x00"},
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
            name="Ray-Ban-1234",
        )
        events = matcher.match(snapshot)
        assert len(events) == 2
        assert events[0].fingerprint.id == "high-score"
        assert events[1].fingerprint.id == "low-score"
        assert events[0].confidence > events[1].confidence

    def test_only_first_fingerprint_matches_second_does_not(self) -> None:
        fp1 = make_fingerprint(
            fp_id="fp-match",
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=40,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        fp2 = make_fingerprint(
            fp_id="fp-no-match",
            manufacturer_ids=[9999],
            service_uuids=[],
            name_patterns=[],
            match_threshold=40,
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=0, name_pattern=0),
        )
        db = make_db_with(fp1, fp2)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(manufacturer_data={1177: b"\x00"}, service_uuids=[], name=None)
        events = matcher.match(snapshot)
        assert len(events) == 1
        assert events[0].fingerprint.id == "fp-match"


# ---------------------------------------------------------------------------
# FingerprintMatcher — global RSSI threshold override
# ---------------------------------------------------------------------------

class TestGlobalRSSIThreshold:
    """Tests for the global_rssi_threshold override."""

    def test_global_threshold_overrides_per_fingerprint_threshold(self) -> None:
        fp = make_fingerprint(rssi_threshold=-70)
        db = make_db_with(fp)
        # Override threshold to -60 (closer required)
        matcher = FingerprintMatcher(db, global_rssi_threshold=-60)
        snapshot = make_snapshot(
            rssi=-65,
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
            name=None,
        )
        events = matcher.match(snapshot)
        # Score = 50 >= match_threshold 40 → should match
        assert len(events) == 1
        event = events[0]
        # With per-fingerprint threshold -70: -65 >= -70 → in proximity
        # With global override -60: -65 >= -60 → False (not in proximity)
        assert event.fingerprint.rssi_threshold == -60
        assert event.is_in_proximity is False

    def test_without_global_threshold_uses_per_fingerprint(self) -> None:
        fp = make_fingerprint(rssi_threshold=-70)
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db, global_rssi_threshold=None)
        snapshot = make_snapshot(
            rssi=-65,
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
            name=None,
        )
        events = matcher.match(snapshot)
        assert len(events) == 1
        event = events[0]
        assert event.fingerprint.rssi_threshold == -70
        assert event.is_in_proximity is True  # -65 >= -70

    def test_global_threshold_does_not_affect_confidence_score(self) -> None:
        fp = make_fingerprint(rssi_threshold=-70, match_threshold=40)
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db, global_rssi_threshold=-50)
        snapshot = make_snapshot(
            rssi=-75,
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
            name=None,
        )
        events = matcher.match(snapshot)
        # Matching is purely based on confidence, RSSI doesn't affect it.
        assert len(events) == 1
        assert events[0].confidence == 50


# ---------------------------------------------------------------------------
# FingerprintMatcher — DetectionEvent properties
# ---------------------------------------------------------------------------

class TestDetectionEventProperties:
    """Tests that the DetectionEvent returned by match() has correct properties."""

    def test_event_matched_fields_correct(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
            name_patterns=["Ray-Ban"],
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            manufacturer_data={1177: b"\x00"},
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
            name="Ray-Ban-ABCD",
        )
        events = matcher.match(snapshot)
        assert len(events) == 1
        assert set(events[0].matched_fields) == {
            "manufacturer_id",
            "service_uuid",
            "name_pattern",
        }

    def test_event_device_is_same_snapshot(self) -> None:
        fp = make_fingerprint()
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot()
        events = matcher.match(snapshot)
        assert events[0].device is snapshot

    def test_event_fingerprint_references_loaded_fingerprint(self) -> None:
        fp = make_fingerprint()
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot()
        events = matcher.match(snapshot)
        assert events[0].fingerprint.id == fp.id

    def test_event_alerted_is_false_by_default(self) -> None:
        fp = make_fingerprint()
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot()
        events = matcher.match(snapshot)
        assert events[0].alerted is False

    def test_event_confidence_percent_computed_correctly(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            weights=ConfidenceWeights(manufacturer_id=50, service_uuid=30, name_pattern=40),
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot(
            manufacturer_data={1177: b"\x00"},
            service_uuids=[],
            name=None,
        )
        events = matcher.match(snapshot)
        # score=50, max=120 → 50/120*100 ≈ 41.7
        assert abs(events[0].confidence_percent - (50 / 120 * 100)) < 0.2


# ---------------------------------------------------------------------------
# FingerprintMatcher — edge cases
# ---------------------------------------------------------------------------

class TestMatcherEdgeCases:
    """Edge-case tests for FingerprintMatcher."""

    def test_empty_database_returns_empty(self) -> None:
        db = make_db_with()
        matcher = FingerprintMatcher(db)
        snapshot = make_snapshot()
        events = matcher.match(snapshot)
        assert events == []

    def test_snapshot_with_no_advertisement_data_returns_empty(self) -> None:
        fp = make_fingerprint()
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = BLEDeviceSnapshot(
            address="AA:BB:CC:DD:EE:FF",
            name=None,
            rssi=-90,
        )
        events = matcher.match(snapshot)
        assert events == []

    def test_snapshot_with_only_name_matches_by_name(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[],
            service_uuids=[],
            name_patterns=["RayBan"],
            match_threshold=40,
            weights=ConfidenceWeights(manufacturer_id=0, service_uuid=0, name_pattern=40),
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        snapshot = BLEDeviceSnapshot(
            address="AA:BB:CC:DD:EE:FF",
            name="RayBan-XYZ",
            rssi=-65,
        )
        events = matcher.match(snapshot)
        assert len(events) == 1
        assert "name_pattern" in events[0].matched_fields

    def test_zero_match_threshold_always_matches_if_candidate(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=0,
        )
        db = make_db_with(fp)
        matcher = FingerprintMatcher(db)
        # Score will be 50 (manufacturer match) which is >= 0
        snapshot = make_snapshot(manufacturer_data={1177: b"\x00"}, service_uuids=[], name=None)
        events = matcher.match(snapshot)
        assert len(events) == 1

    def test_match_against_bundled_database_meta_ray_ban(self) -> None:
        """Integration smoke test: a simulated Meta Ray-Ban device should match."""
        db = FingerprintDatabase.load()
        matcher = FingerprintMatcher(db)
        snapshot = BLEDeviceSnapshot(
            address="AA:BB:CC:DD:EE:FF",
            name="Ray-Ban-1234",
            rssi=-65,
            manufacturer_data={1177: b"\x00\x01"},
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
        )
        events = matcher.match(snapshot)
        assert len(events) > 0
        fingerprint_ids = {e.fingerprint.id for e in events}
        assert "meta-ray-ban-gen2" in fingerprint_ids

    def test_match_against_bundled_database_bose_frames(self) -> None:
        """Integration smoke test: a simulated Bose Frames device should match."""
        db = FingerprintDatabase.load()
        matcher = FingerprintMatcher(db)
        snapshot = BLEDeviceSnapshot(
            address="BB:CC:DD:EE:FF:AA",
            name="Bose Frames Alto",
            rssi=-60,
            manufacturer_data={2291: b"\x00"},
            service_uuids=["0000febe-0000-1000-8000-00805f9b34fb"],
        )
        events = matcher.match(snapshot)
        assert len(events) > 0
        fingerprint_ids = {e.fingerprint.id for e in events}
        assert "bose-frames-alto" in fingerprint_ids

    def test_match_against_bundled_database_unknown_device_no_match(self) -> None:
        """An unknown device with no matching fields should produce no events."""
        db = FingerprintDatabase.load()
        matcher = FingerprintMatcher(db)
        snapshot = BLEDeviceSnapshot(
            address="CC:DD:EE:FF:AA:BB",
            name="Generic BLE Headphones",
            rssi=-75,
            manufacturer_data={65535: b"\xff\xff"},
            service_uuids=["00000000-0000-0000-0000-000000000000"],
        )
        events = matcher.match(snapshot)
        assert events == []
