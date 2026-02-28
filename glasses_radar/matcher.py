"""Fingerprint matching logic for glasses_radar.

This module compares BLE advertisement data (captured as
:class:`~glasses_radar.models.BLEDeviceSnapshot` objects) against the known
device signatures in the fingerprint database, producing confidence-scored
:class:`~glasses_radar.models.DetectionEvent` objects for every match that
meets or exceeds the configured threshold.

Typical usage::

    from glasses_radar.fingerprints import FingerprintDatabase
    from glasses_radar.matcher import FingerprintMatcher

    db = FingerprintDatabase.load()
    matcher = FingerprintMatcher(db)

    events = matcher.match(snapshot)
    for event in events:
        print(event.fingerprint.name, event.confidence_percent)
"""

from __future__ import annotations

import logging
from typing import Sequence

from glasses_radar.fingerprints import FingerprintDatabase
from glasses_radar.models import BLEDeviceSnapshot, DetectionEvent, Fingerprint

logger = logging.getLogger(__name__)


class FingerprintMatcher:
    """Matches BLE advertisement snapshots against the fingerprint database.

    For each incoming :class:`~glasses_radar.models.BLEDeviceSnapshot` the
    matcher:

    1. Identifies candidate fingerprints using the database's index helpers
       (manufacturer ID, service UUID, or name pattern).
    2. Scores each candidate by accumulating confidence points for every
       matching field, using the per-fingerprint
       :class:`~glasses_radar.models.ConfidenceWeights`.
    3. Returns a :class:`~glasses_radar.models.DetectionEvent` for every
       candidate whose accumulated score meets or exceeds its
       ``match_threshold``.

    Attributes:
        db: The :class:`~glasses_radar.fingerprints.FingerprintDatabase`
            instance used for candidate lookup.
        global_rssi_threshold: An optional global RSSI threshold (dBm) that
            overrides per-fingerprint thresholds when set.
    """

    def __init__(
        self,
        db: FingerprintDatabase,
        global_rssi_threshold: int | None = None,
    ) -> None:
        """Initialise the matcher with a fingerprint database.

        Args:
            db: Loaded and validated :class:`~glasses_radar.fingerprints.FingerprintDatabase`.
            global_rssi_threshold: When provided, overrides per-fingerprint
                RSSI thresholds for proximity checks.  Does **not** affect
                confidence scoring — only the ``is_in_proximity`` property on
                resulting :class:`~glasses_radar.models.DetectionEvent` objects.
        """
        self.db = db
        self.global_rssi_threshold = global_rssi_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(self, snapshot: BLEDeviceSnapshot) -> list[DetectionEvent]:
        """Match a BLE advertisement snapshot against all enabled fingerprints.

        Args:
            snapshot: A :class:`~glasses_radar.models.BLEDeviceSnapshot`
                representing a single received BLE advertisement.

        Returns:
            A (possibly empty) list of :class:`~glasses_radar.models.DetectionEvent`
            objects, one per fingerprint whose confidence score meets the
            ``match_threshold``.  Results are ordered by descending confidence
            score.
        """
        candidates = self.db.get_candidates(
            manufacturer_ids=snapshot.manufacturer_ids or None,
            service_uuids=snapshot.service_uuids or None,
            device_name=snapshot.name if snapshot.name else None,
            enabled_only=True,
        )

        # If no candidates were found through the index, also try a full scan
        # of enabled fingerprints to catch devices that only match by name
        # (where the initial get_candidates call may return nothing because
        # both manufacturer_ids and service_uuids are empty).
        if not candidates and snapshot.name:
            candidates = self.db.get_by_name_pattern(snapshot.name, enabled_only=True)

        if not candidates:
            return []

        events: list[DetectionEvent] = []
        for fingerprint in candidates:
            event = self._score_fingerprint(snapshot, fingerprint)
            if event is not None:
                events.append(event)

        # Sort by descending confidence so the best match comes first.
        events.sort(key=lambda e: e.confidence, reverse=True)

        logger.debug(
            "Snapshot %s: %d candidate(s) evaluated, %d match(es) found",
            snapshot.address,
            len(candidates),
            len(events),
        )

        return events

    def score_against(
        self, snapshot: BLEDeviceSnapshot, fingerprint: Fingerprint
    ) -> tuple[int, list[str]]:
        """Compute the confidence score for a specific fingerprint against a snapshot.

        This low-level helper is exposed for testing and introspection.  It does
        **not** check whether the score meets ``match_threshold``.

        Args:
            snapshot: The BLE advertisement snapshot to evaluate.
            fingerprint: The fingerprint to score against.

        Returns:
            A two-tuple ``(score, matched_fields)`` where *score* is the
            accumulated integer confidence score and *matched_fields* is the
            list of field names that contributed points.
        """
        score = 0
        matched_fields: list[str] = []

        # --- Manufacturer ID matching ---
        if fingerprint.manufacturer_ids and snapshot.manufacturer_ids:
            fp_mids = set(fingerprint.manufacturer_ids)
            device_mids = set(snapshot.manufacturer_ids)
            if fp_mids & device_mids:
                score += fingerprint.confidence_weights.manufacturer_id
                matched_fields.append("manufacturer_id")

        # --- Service UUID matching ---
        if fingerprint.service_uuids and snapshot.service_uuids:
            fp_uuids = {u.lower() for u in fingerprint.service_uuids}
            device_uuids = {u.lower() for u in snapshot.service_uuids}
            if fp_uuids & device_uuids:
                score += fingerprint.confidence_weights.service_uuid
                matched_fields.append("service_uuid")

        # --- Name pattern matching ---
        if fingerprint.name_patterns and snapshot.name:
            device_name_lower = snapshot.name.lower()
            for pattern in fingerprint.name_patterns:
                if pattern.lower() in device_name_lower:
                    score += fingerprint.confidence_weights.name_pattern
                    matched_fields.append("name_pattern")
                    break  # Award the weight at most once per fingerprint

        return score, matched_fields

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _score_fingerprint(
        self, snapshot: BLEDeviceSnapshot, fingerprint: Fingerprint
    ) -> DetectionEvent | None:
        """Score a snapshot against one fingerprint and return an event if matched.

        Args:
            snapshot: The incoming BLE advertisement snapshot.
            fingerprint: The candidate fingerprint to evaluate.

        Returns:
            A :class:`~glasses_radar.models.DetectionEvent` if the accumulated
            score meets ``match_threshold``, or ``None`` otherwise.
        """
        score, matched_fields = self.score_against(snapshot, fingerprint)

        if score < fingerprint.match_threshold:
            logger.debug(
                "Snapshot %s: fingerprint '%s' scored %d (threshold %d) — no match",
                snapshot.address,
                fingerprint.id,
                score,
                fingerprint.match_threshold,
            )
            return None

        # Apply global RSSI threshold override if set.
        effective_fingerprint = fingerprint
        if self.global_rssi_threshold is not None:
            # Build a lightweight patched fingerprint with the overridden threshold.
            effective_fingerprint = _PatchedRSSIFingerprint(
                fingerprint, self.global_rssi_threshold
            )

        event = DetectionEvent(
            device=snapshot,
            fingerprint=effective_fingerprint,
            confidence=score,
            matched_fields=matched_fields,
        )

        logger.debug(
            "Snapshot %s: fingerprint '%s' MATCHED score=%d fields=%s",
            snapshot.address,
            fingerprint.id,
            score,
            matched_fields,
        )

        return event


class _PatchedRSSIFingerprint(Fingerprint):
    """Internal subclass that overrides rssi_threshold without copying all data.

    Used by :class:`FingerprintMatcher` to apply a global RSSI override to the
    resulting :class:`~glasses_radar.models.DetectionEvent` without mutating
    the original :class:`Fingerprint` object stored in the database.
    """

    def __new__(cls, base: Fingerprint, rssi_threshold: int) -> "_PatchedRSSIFingerprint":  # type: ignore[override]
        """Bypass the dataclass __init__ and copy fields from *base*."""
        # Create without calling __init__ by using object.__new__ and then
        # manually setting all fields to match the base fingerprint.
        instance = object.__new__(cls)
        # Copy all dataclass fields from the base instance.
        for f_name in base.__dataclass_fields__:  # type: ignore[attr-defined]
            object.__setattr__(instance, f_name, getattr(base, f_name))
        # Override the threshold.
        object.__setattr__(instance, "rssi_threshold", rssi_threshold)
        return instance

    def __init__(self, base: Fingerprint, rssi_threshold: int) -> None:  # noqa: D401
        """No-op; initialisation is done in __new__."""
        # Do NOT call super().__init__ — the object is already fully set up.
        pass
