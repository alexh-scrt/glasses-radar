"""Unit tests for glasses_radar.scanner.

These tests mock the Bleak BleakScanner to avoid requiring a physical
Bluetooth adapter.  They verify:

- BLEDeviceSnapshot construction from Bleak data.
- RSSI pre-filtering.
- Verbose mode callback.
- Matcher and alerter integration.
- Statistics tracking.
- Scanner stop/run lifecycle.
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from glasses_radar.alerter import Alerter
from glasses_radar.fingerprints import FingerprintDatabase
from glasses_radar.matcher import FingerprintMatcher
from glasses_radar.models import (
    BLEDeviceSnapshot,
    ConfidenceWeights,
    DetectionEvent,
    Fingerprint,
)
from glasses_radar.scanner import BLEScanner, ScannerError, build_scanner


# ---------------------------------------------------------------------------
# Mock Bleak types
# ---------------------------------------------------------------------------

class MockBLEDevice:
    """Minimal mock of bleak.backends.device.BLEDevice."""

    def __init__(
        self,
        address: str = "AA:BB:CC:DD:EE:FF",
        name: str | None = "TestDevice",
        rssi: int = -65,
    ) -> None:
        self.address = address
        self.name = name
        self.rssi = rssi


class MockAdvertisementData:
    """Minimal mock of bleak.backends.scanner.AdvertisementData."""

    def __init__(
        self,
        local_name: str | None = None,
        rssi: int = -65,
        manufacturer_data: dict | None = None,
        service_uuids: list[str] | None = None,
        service_data: dict | None = None,
        tx_power: int | None = None,
    ) -> None:
        self.local_name = local_name
        self.rssi = rssi
        self.manufacturer_data = manufacturer_data or {}
        self.service_uuids = service_uuids or []
        self.service_data = service_data or {}
        self.tx_power = tx_power


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_fingerprint(
    fp_id: str = "test-fp",
    manufacturer_ids: list[int] | None = None,
    service_uuids: list[str] | None = None,
    name_patterns: list[str] | None = None,
    match_threshold: int = 40,
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
        rssi_threshold=-70,
        confidence_weights=ConfidenceWeights(
            manufacturer_id=50, service_uuid=30, name_pattern=40
        ),
        match_threshold=match_threshold,
    )


def make_db_with(*fingerprints: Fingerprint) -> FingerprintDatabase:
    """Construct a FingerprintDatabase directly from Fingerprint objects."""
    return FingerprintDatabase(
        version="1.0.0",
        description="Test DB",
        last_updated="2024-01-01",
        fingerprints=list(fingerprints),
    )


def make_matcher(*fingerprints: Fingerprint) -> FingerprintMatcher:
    """Build a FingerprintMatcher with given fingerprints."""
    db = make_db_with(*fingerprints)
    return FingerprintMatcher(db)


def make_alerter() -> tuple[Alerter, list[DetectionEvent]]:
    """Build an Alerter with mocked on_detection that records calls."""
    detected: list[DetectionEvent] = []

    from io import StringIO
    from rich.console import Console
    console = Console(file=StringIO(), force_terminal=False)
    alerter = Alerter(cooldown=0, sound=False, console=console)
    # Patch on_detection to capture calls.
    original = alerter.on_detection

    def capturing_on_detection(event: DetectionEvent) -> bool:
        detected.append(event)
        return original(event)

    alerter.on_detection = capturing_on_detection  # type: ignore[method-assign]
    return alerter, detected


# ---------------------------------------------------------------------------
# BLEScanner._build_snapshot
# ---------------------------------------------------------------------------

class TestBuildSnapshot:
    """Tests for the static _build_snapshot method."""

    def test_address_is_set(self) -> None:
        device = MockBLEDevice(address="AA:BB:CC:DD:EE:FF")
        adv = MockAdvertisementData()
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.address == "AA:BB:CC:DD:EE:FF"

    def test_rssi_is_set(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-72)
        snap = BLEScanner._build_snapshot(device, adv, rssi=-72)  # type: ignore[arg-type]
        assert snap.rssi == -72

    def test_local_name_preferred_over_device_name(self) -> None:
        device = MockBLEDevice(name="DeviceName")
        adv = MockAdvertisementData(local_name="AdvName")
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.name == "AdvName"

    def test_device_name_used_when_no_local_name(self) -> None:
        device = MockBLEDevice(name="DeviceName")
        adv = MockAdvertisementData(local_name=None)
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.name == "DeviceName"

    def test_name_is_none_when_neither_name_present(self) -> None:
        device = MockBLEDevice(name=None)
        adv = MockAdvertisementData(local_name=None)
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.name is None

    def test_manufacturer_data_copied(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(manufacturer_data={1177: b"\x00\x01"})
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.manufacturer_data == {1177: b"\x00\x01"}

    def test_service_uuids_copied(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"]
        )
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert "0000fe59-0000-1000-8000-00805f9b34fb" in snap.service_uuids

    def test_service_data_copied(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(
            service_data={"0000fe59-0000-1000-8000-00805f9b34fb": b"\x01\x02"}
        )
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert "0000fe59-0000-1000-8000-00805f9b34fb" in snap.service_data

    def test_tx_power_copied(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(tx_power=-10)
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.tx_power == -10

    def test_tx_power_none_when_not_present(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(tx_power=None)
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.tx_power is None

    def test_timestamp_is_utc(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData()
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.timestamp.tzinfo is datetime.timezone.utc

    def test_empty_manufacturer_data_produces_empty_dict(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(manufacturer_data={})
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.manufacturer_data == {}

    def test_empty_service_uuids_produces_empty_list(self) -> None:
        device = MockBLEDevice()
        adv = MockAdvertisementData(service_uuids=[])
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.service_uuids == []

    def test_address_normalised_to_uppercase(self) -> None:
        device = MockBLEDevice(address="aa:bb:cc:dd:ee:ff")
        adv = MockAdvertisementData()
        snap = BLEScanner._build_snapshot(device, adv, rssi=-65)  # type: ignore[arg-type]
        assert snap.address == "AA:BB:CC:DD:EE:FF"


# ---------------------------------------------------------------------------
# BLEScanner._detection_callback — unit-level tests
# ---------------------------------------------------------------------------

class TestDetectionCallback:
    """Tests for _detection_callback with mocked matcher and alerter."""

    def _make_scanner(
        self,
        fingerprints: list[Fingerprint] | None = None,
        rssi_threshold: int | None = None,
        verbose: bool = False,
        verbose_callback=None,
    ) -> tuple[BLEScanner, Alerter, list[DetectionEvent]]:
        fps = fingerprints or [make_fingerprint()]
        matcher = make_matcher(*fps)
        alerter, detected = make_alerter()
        scanner = BLEScanner(
            matcher=matcher,
            alerter=alerter,
            verbose=verbose,
            rssi_threshold=rssi_threshold,
            verbose_callback=verbose_callback,
        )
        return scanner, alerter, detected

    def test_advertisement_seen_counter_incremented(self) -> None:
        scanner, _, _ = self._make_scanner()
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-65)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_seen == 1

    def test_rssi_prefilter_drops_weak_advertisement(self) -> None:
        scanner, _, detected = self._make_scanner(rssi_threshold=-70)
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-80)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_skipped_rssi == 1
        assert detected == []

    def test_rssi_prefilter_passes_strong_advertisement(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177], service_uuids=[], name_patterns=[]
        )
        scanner, _, _ = self._make_scanner(
            fingerprints=[fp], rssi_threshold=-70
        )
        device = MockBLEDevice()
        adv = MockAdvertisementData(
            rssi=-65, manufacturer_data={1177: b"\x00"}
        )
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_skipped_rssi == 0

    def test_rssi_prefilter_none_passes_all(self) -> None:
        scanner, _, _ = self._make_scanner(rssi_threshold=None)
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-100)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_skipped_rssi == 0

    def test_rssi_at_exact_threshold_passes(self) -> None:
        scanner, _, _ = self._make_scanner(rssi_threshold=-70)
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-70)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_skipped_rssi == 0

    def test_matching_advertisement_calls_on_detection(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177],
            service_uuids=[],
            name_patterns=[],
            match_threshold=40,
        )
        scanner, alerter, detected = self._make_scanner(fingerprints=[fp])
        device = MockBLEDevice(name=None)
        adv = MockAdvertisementData(
            rssi=-65, manufacturer_data={1177: b"\x00"}
        )
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert len(detected) == 1
        assert detected[0].fingerprint.id == "test-fp"

    def test_non_matching_advertisement_no_detection(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[9999], service_uuids=[], name_patterns=[]
        )
        scanner, _, detected = self._make_scanner(fingerprints=[fp])
        device = MockBLEDevice(name=None)
        adv = MockAdvertisementData(
            rssi=-65, manufacturer_data={1234: b"\x00"}
        )
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert detected == []

    def test_advertisements_matched_counter_incremented(self) -> None:
        fp = make_fingerprint(
            manufacturer_ids=[1177], service_uuids=[], name_patterns=[], match_threshold=40
        )
        scanner, _, _ = self._make_scanner(fingerprints=[fp])
        device = MockBLEDevice(name=None)
        adv = MockAdvertisementData(
            rssi=-65, manufacturer_data={1177: b"\x00"}
        )
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_matched == 1

    def test_non_matching_does_not_increment_matched_counter(self) -> None:
        scanner, _, _ = self._make_scanner()
        device = MockBLEDevice(name="RandomDevice")
        adv = MockAdvertisementData(
            rssi=-65, manufacturer_data={9999: b"\x00"}, service_uuids=[]
        )
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_matched == 0

    def test_verbose_mode_calls_on_verbose_device(self) -> None:
        from io import StringIO
        from rich.console import Console
        console = Console(file=StringIO(), force_terminal=False)
        alerter = Alerter(cooldown=0, sound=False, console=console)
        verbose_calls: list[dict] = []

        original_verbose = alerter.on_verbose_device

        def capturing_verbose(address, name, rssi, **kwargs):
            verbose_calls.append({"address": address, "name": name, "rssi": rssi})
            return original_verbose(address, name, rssi, **kwargs)

        alerter.on_verbose_device = capturing_verbose  # type: ignore[method-assign]

        matcher = make_matcher(make_fingerprint())
        scanner = BLEScanner(
            matcher=matcher, alerter=alerter, verbose=True, sound=False
        )
        device = MockBLEDevice(address="AA:BB:CC:DD:EE:FF", name="SomeDevice")
        adv = MockAdvertisementData(rssi=-65)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert len(verbose_calls) == 1
        assert verbose_calls[0]["address"] == "AA:BB:CC:DD:EE:FF"

    def test_verbose_mode_false_does_not_call_on_verbose_device(self) -> None:
        from io import StringIO
        from rich.console import Console
        console = Console(file=StringIO(), force_terminal=False)
        alerter = Alerter(cooldown=0, sound=False, console=console)
        verbose_calls: list = []
        alerter.on_verbose_device = lambda *a, **kw: verbose_calls.append(1)  # type: ignore[method-assign]

        matcher = make_matcher(make_fingerprint())
        scanner = BLEScanner(
            matcher=matcher, alerter=alerter, verbose=False
        )
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-65)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert verbose_calls == []

    def test_verbose_callback_called_for_every_advertisement(self) -> None:
        fp = make_fingerprint()
        matcher = make_matcher(fp)
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        snapshots: list[BLEDeviceSnapshot] = []
        scanner = BLEScanner(
            matcher=matcher,
            alerter=alerter,
            verbose_callback=snapshots.append,
        )
        device = MockBLEDevice(name="SomeDevice")
        adv = MockAdvertisementData(rssi=-65, manufacturer_data={9999: b"\x00"})
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert len(snapshots) == 1
        assert snapshots[0].address == "AA:BB:CC:DD:EE:FF"

    def test_verbose_callback_not_called_when_rssi_filtered(self) -> None:
        fp = make_fingerprint()
        matcher = make_matcher(fp)
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        snapshots: list[BLEDeviceSnapshot] = []
        scanner = BLEScanner(
            matcher=matcher,
            alerter=alerter,
            rssi_threshold=-70,
            verbose_callback=snapshots.append,
        )
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-90)  # weaker than threshold
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        # Dropped by RSSI filter before callback
        assert snapshots == []

    def test_advertisement_rssi_from_adv_data_not_device(self) -> None:
        """RSSI should be read from advertisement_data.rssi, not device.rssi."""
        fp = make_fingerprint(
            manufacturer_ids=[1177], service_uuids=[], name_patterns=[], match_threshold=1
        )
        matcher = make_matcher(fp)
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        snapshots: list[BLEDeviceSnapshot] = []
        scanner = BLEScanner(
            matcher=matcher,
            alerter=alerter,
            verbose_callback=snapshots.append,
        )
        device = MockBLEDevice(rssi=-99)  # device.rssi is ignored
        adv = MockAdvertisementData(
            rssi=-55, manufacturer_data={1177: b"\x00"}
        )
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert len(snapshots) == 1
        assert snapshots[0].rssi == -55  # from adv_data, not device

    def test_multiple_callbacks_for_multiple_advertisements(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        snapshots: list[BLEDeviceSnapshot] = []
        scanner = BLEScanner(
            matcher=matcher,
            alerter=alerter,
            verbose_callback=snapshots.append,
        )
        for i in range(5):
            device = MockBLEDevice(address=f"AA:BB:CC:DD:EE:{i:02X}")
            adv = MockAdvertisementData(rssi=-65)
            scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert len(snapshots) == 5
        assert scanner.advertisements_seen == 5


# ---------------------------------------------------------------------------
# BLEScanner statistics
# ---------------------------------------------------------------------------

class TestScannerStatistics:
    """Tests for scanner statistics tracking."""

    def _make_scanner(self) -> BLEScanner:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        return BLEScanner(matcher=matcher, alerter=alerter)

    def test_initial_stats_are_zero(self) -> None:
        scanner = self._make_scanner()
        assert scanner.advertisements_seen == 0
        assert scanner.advertisements_matched == 0
        assert scanner.advertisements_skipped_rssi == 0

    def test_get_stats_returns_dict(self) -> None:
        scanner = self._make_scanner()
        stats = scanner.get_stats()
        assert isinstance(stats, dict)
        assert "advertisements_seen" in stats
        assert "advertisements_matched" in stats
        assert "advertisements_skipped_rssi" in stats

    def test_stats_reflect_callbacks(self) -> None:
        scanner = self._make_scanner()
        scanner.rssi_threshold = -70
        for rssi in [-65, -65, -80, -65]:  # -80 should be filtered
            device = MockBLEDevice()
            adv = MockAdvertisementData(rssi=rssi)
            scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_seen == 4
        assert scanner.advertisements_skipped_rssi == 1

    def test_is_running_false_initially(self) -> None:
        scanner = self._make_scanner()
        assert scanner.is_running is False


# ---------------------------------------------------------------------------
# BLEScanner.run — async lifecycle tests (with mocked BleakScanner)
# ---------------------------------------------------------------------------

class TestScannerRun:
    """Tests for BLEScanner.run() using a mocked BleakScanner."""

    def _make_scanner(
        self, rssi_threshold: int | None = None, verbose: bool = False
    ) -> BLEScanner:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        return BLEScanner(
            matcher=matcher,
            alerter=alerter,
            rssi_threshold=rssi_threshold,
            verbose=verbose,
            scan_interval=0.05,  # Short interval for tests
        )

    @pytest.mark.asyncio
    async def test_run_with_duration_completes(self) -> None:
        """Scanner should complete after the specified duration."""
        scanner = self._make_scanner()
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock()
        mock_bleak.stop = AsyncMock()

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            await asyncio.wait_for(scanner.run(duration=0.05), timeout=2.0)

        mock_bleak.start.assert_called_once()
        mock_bleak.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sets_is_running(self) -> None:
        """is_running should be True during scan."""
        scanner = self._make_scanner()
        running_states: list[bool] = []
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock()
        mock_bleak.stop = AsyncMock()

        def on_start():
            running_states.append(scanner.is_running)

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            await asyncio.wait_for(
                scanner.run(duration=0.05, on_start=on_start), timeout=2.0
            )

        assert True in running_states
        assert scanner.is_running is False  # False after completion

    @pytest.mark.asyncio
    async def test_run_calls_on_start_callback(self) -> None:
        scanner = self._make_scanner()
        calls: list[str] = []
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock()
        mock_bleak.stop = AsyncMock()

        def on_start():
            calls.append("start")

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            await asyncio.wait_for(
                scanner.run(duration=0.05, on_start=on_start), timeout=2.0
            )

        assert calls == ["start"]

    @pytest.mark.asyncio
    async def test_run_calls_on_stop_callback(self) -> None:
        scanner = self._make_scanner()
        calls: list[str] = []
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock()
        mock_bleak.stop = AsyncMock()

        def on_stop():
            calls.append("stop")

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            await asyncio.wait_for(
                scanner.run(duration=0.05, on_stop=on_stop), timeout=2.0
            )

        assert calls == ["stop"]

    @pytest.mark.asyncio
    async def test_run_raises_scanner_error_on_bleak_start_failure(self) -> None:
        scanner = self._make_scanner()
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock(side_effect=Exception("Bluetooth not available"))
        mock_bleak.stop = AsyncMock()

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            with pytest.raises(ScannerError, match="Failed to start BLE scanner"):
                await scanner.run(duration=1.0)

    @pytest.mark.asyncio
    async def test_stop_method_terminates_indefinite_scan(self) -> None:
        """Calling stop() should terminate an indefinite scan."""
        scanner = self._make_scanner()
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock()
        mock_bleak.stop = AsyncMock()

        async def run_and_stop():
            run_task = asyncio.create_task(scanner.run(duration=0))
            # Give the scanner a moment to start.
            await asyncio.sleep(0.1)
            scanner.stop()
            await asyncio.wait_for(run_task, timeout=2.0)

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            await run_and_stop()

        assert scanner.is_running is False
        mock_bleak.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_reset_stats_on_each_run(self) -> None:
        """Stats should reset at the start of each run() call."""
        scanner = self._make_scanner(rssi_threshold=-70)
        mock_bleak = MagicMock()
        mock_bleak.start = AsyncMock()
        mock_bleak.stop = AsyncMock()

        # Simulate some callbacks before run to populate stats.
        device = MockBLEDevice()
        adv = MockAdvertisementData(rssi=-80)
        scanner._detection_callback(device, adv)  # type: ignore[arg-type]
        assert scanner.advertisements_seen == 1

        with patch("glasses_radar.scanner.BleakScanner", return_value=mock_bleak):
            await asyncio.wait_for(scanner.run(duration=0.05), timeout=2.0)

        # After run() completes, stats should reflect only the run period.
        assert scanner.advertisements_seen == 0

    @pytest.mark.asyncio
    async def test_detection_callback_registered_with_bleak(self) -> None:
        """BleakScanner should be constructed with our detection callback."""
        scanner = self._make_scanner()
        constructor_kwargs: dict = {}
        mock_instance = MagicMock()
        mock_instance.start = AsyncMock()
        mock_instance.stop = AsyncMock()

        def capture_constructor(**kwargs):
            constructor_kwargs.update(kwargs)
            return mock_instance

        with patch("glasses_radar.scanner.BleakScanner", side_effect=capture_constructor):
            await asyncio.wait_for(scanner.run(duration=0.05), timeout=2.0)

        assert "detection_callback" in constructor_kwargs
        assert constructor_kwargs["detection_callback"] == scanner._detection_callback


# ---------------------------------------------------------------------------
# build_scanner factory
# ---------------------------------------------------------------------------

class TestBuildScanner:
    """Tests for the build_scanner convenience factory."""

    def test_returns_ble_scanner_instance(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        scanner = build_scanner(matcher, alerter)
        assert isinstance(scanner, BLEScanner)

    def test_scanner_has_correct_matcher_and_alerter(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        scanner = build_scanner(matcher, alerter)
        assert scanner.matcher is matcher
        assert scanner.alerter is alerter

    def test_scanner_verbose_flag_set(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        scanner = build_scanner(matcher, alerter, verbose=True)
        assert scanner.verbose is True

    def test_scanner_rssi_threshold_set(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        scanner = build_scanner(matcher, alerter, rssi_threshold=-60)
        assert scanner.rssi_threshold == -60

    def test_scanner_verbose_callback_set(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        cb = lambda snap: None  # noqa: E731
        scanner = build_scanner(matcher, alerter, verbose_callback=cb)
        assert scanner._verbose_callback is cb

    def test_scanner_scan_interval_set(self) -> None:
        matcher = make_matcher(make_fingerprint())
        from io import StringIO
        from rich.console import Console
        alerter = Alerter(
            cooldown=0,
            sound=False,
            console=Console(file=StringIO(), force_terminal=False),
        )
        scanner = build_scanner(matcher, alerter, scan_interval=5.0)
        assert scanner.scan_interval == 5.0
