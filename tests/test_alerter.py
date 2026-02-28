"""Unit tests for glasses_radar.alerter.

Covers Alerter cooldown logic, detection card rendering, log file output,
RSSI/confidence bar helpers, and verbose device output.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import tempfile

import pytest

from rich.console import Console
from io import StringIO

from glasses_radar.alerter import (
    Alerter,
    _confidence_bar,
    _confidence_color,
    _rssi_bar,
    _rssi_label,
    _rssi_color,
)
from glasses_radar.models import (
    BLEDeviceSnapshot,
    ConfidenceWeights,
    DetectionEvent,
    Fingerprint,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_console() -> tuple[Console, StringIO]:
    """Return a Rich Console writing to a StringIO buffer for testing."""
    buf = StringIO()
    # force_terminal=False strips ANSI codes, making assertions easier.
    console = Console(file=buf, force_terminal=False, highlight=False, markup=False)
    return console, buf


def make_fingerprint(
    fp_id: str = "meta-ray-ban-gen2",
    rssi_threshold: int = -70,
    match_threshold: int = 40,
) -> Fingerprint:
    """Build a Fingerprint for testing."""
    return Fingerprint(
        id=fp_id,
        name="Meta Ray-Ban Smart Glasses (Gen 2)",
        vendor="Meta / EssilorLuxottica",
        manufacturer_ids=[1177],
        service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
        name_patterns=["Ray-Ban", "RayBan"],
        rssi_threshold=rssi_threshold,
        confidence_weights=ConfidenceWeights(manufacturer_id=50, service_uuid=30, name_pattern=40),
        match_threshold=match_threshold,
    )


def make_snapshot(
    address: str = "AA:BB:CC:DD:EE:FF",
    name: str | None = "RayBan-1234",
    rssi: int = -62,
) -> BLEDeviceSnapshot:
    """Build a BLEDeviceSnapshot for testing."""
    return BLEDeviceSnapshot(
        address=address,
        name=name,
        rssi=rssi,
        manufacturer_data={1177: b"\x00\x01"},
        service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
    )


def make_event(
    confidence: int = 80,
    matched_fields: list[str] | None = None,
    address: str = "AA:BB:CC:DD:EE:FF",
    rssi: int = -62,
    rssi_threshold: int = -70,
) -> DetectionEvent:
    """Build a DetectionEvent for testing."""
    return DetectionEvent(
        device=make_snapshot(address=address, rssi=rssi),
        fingerprint=make_fingerprint(rssi_threshold=rssi_threshold),
        confidence=confidence,
        matched_fields=matched_fields or ["manufacturer_id", "name_pattern"],
    )


# ---------------------------------------------------------------------------
# Alerter — construction
# ---------------------------------------------------------------------------

class TestAlerterConstruction:
    """Tests for Alerter.__init__."""

    def test_default_values(self) -> None:
        alerter = Alerter()
        assert alerter.rssi_threshold is None
        assert alerter.cooldown == 30
        assert alerter.sound is True
        assert alerter.log_path is None

    def test_custom_values(self) -> None:
        alerter = Alerter(rssi_threshold=-60, cooldown=60, sound=False)
        assert alerter.rssi_threshold == -60
        assert alerter.cooldown == 60
        assert alerter.sound is False

    def test_log_path_converted_to_path_object(self) -> None:
        alerter = Alerter(log_path="/tmp/test.json")
        assert isinstance(alerter.log_path, pathlib.Path)
        assert str(alerter.log_path) == "/tmp/test.json"

    def test_log_path_none_stays_none(self) -> None:
        alerter = Alerter(log_path=None)
        assert alerter.log_path is None

    def test_custom_console_used(self) -> None:
        console, _ = make_console()
        alerter = Alerter(console=console)
        assert alerter.console is console


# ---------------------------------------------------------------------------
# Alerter — cooldown logic
# ---------------------------------------------------------------------------

class TestAlerterCooldown:
    """Tests for per-device alert cooldown."""

    def test_first_detection_not_in_cooldown(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event = make_event(address="AA:BB:CC:DD:EE:FF")
        assert alerter.is_in_cooldown("AA:BB:CC:DD:EE:FF") is False

    def test_after_alert_device_is_in_cooldown(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event = make_event(address="AA:BB:CC:DD:EE:FF")
        result = alerter.on_detection(event)
        assert result is True
        assert alerter.is_in_cooldown("AA:BB:CC:DD:EE:FF") is True

    def test_second_alert_suppressed_by_cooldown(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event1 = make_event(address="AA:BB:CC:DD:EE:FF")
        event2 = make_event(address="AA:BB:CC:DD:EE:FF")
        alerter.on_detection(event1)
        result = alerter.on_detection(event2)
        assert result is False

    def test_different_addresses_not_affected_by_each_others_cooldown(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event_a = make_event(address="AA:BB:CC:DD:EE:FF")
        event_b = make_event(address="11:22:33:44:55:66")
        alerter.on_detection(event_a)
        result_b = alerter.on_detection(event_b)
        assert result_b is True

    def test_zero_cooldown_never_suppresses(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        for _ in range(3):
            event = make_event(address="AA:BB:CC:DD:EE:FF")
            result = alerter.on_detection(event)
            assert result is True

    def test_reset_cooldown_allows_alert_again(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event1 = make_event(address="AA:BB:CC:DD:EE:FF")
        alerter.on_detection(event1)
        alerter.reset_cooldown("AA:BB:CC:DD:EE:FF")
        assert alerter.is_in_cooldown("AA:BB:CC:DD:EE:FF") is False
        event2 = make_event(address="AA:BB:CC:DD:EE:FF")
        result = alerter.on_detection(event2)
        assert result is True

    def test_reset_all_cooldowns(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        alerter.on_detection(make_event(address="AA:BB:CC:DD:EE:FF"))
        alerter.on_detection(make_event(address="11:22:33:44:55:66"))
        alerter.reset_all_cooldowns()
        assert alerter.is_in_cooldown("AA:BB:CC:DD:EE:FF") is False
        assert alerter.is_in_cooldown("11:22:33:44:55:66") is False

    def test_reset_cooldown_case_insensitive(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        alerter.on_detection(make_event(address="AA:BB:CC:DD:EE:FF"))
        alerter.reset_cooldown("aa:bb:cc:dd:ee:ff")
        assert alerter.is_in_cooldown("AA:BB:CC:DD:EE:FF") is False

    def test_is_in_cooldown_false_for_unknown_address(self) -> None:
        alerter = Alerter(cooldown=30, sound=False)
        assert alerter.is_in_cooldown("00:00:00:00:00:00") is False


# ---------------------------------------------------------------------------
# Alerter — on_detection return value and event mutation
# ---------------------------------------------------------------------------

class TestOnDetection:
    """Tests for on_detection() behaviour."""

    def test_on_detection_returns_true_on_new_alert(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event = make_event()
        assert alerter.on_detection(event) is True

    def test_on_detection_sets_alerted_flag(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event = make_event()
        assert event.alerted is False
        alerter.on_detection(event)
        assert event.alerted is True

    def test_on_detection_suppressed_does_not_set_alerted(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        event1 = make_event()
        alerter.on_detection(event1)
        event2 = make_event()  # same address by default
        alerter.on_detection(event2)
        # event2 was suppressed — alerted should remain False
        assert event2.alerted is False

    def test_on_detection_returns_false_when_suppressed(self) -> None:
        console, _ = make_console()
        alerter = Alerter(cooldown=30, sound=False, console=console)
        alerter.on_detection(make_event())
        result = alerter.on_detection(make_event())
        assert result is False


# ---------------------------------------------------------------------------
# Alerter — JSON log output
# ---------------------------------------------------------------------------

class TestAlerterLogOutput:
    """Tests for JSON log file writing."""

    def test_log_entry_written_on_detection(self) -> None:
        console, _ = make_console()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = pathlib.Path(tmp.name)

        try:
            alerter = Alerter(
                cooldown=0, sound=False, console=console, log_path=tmp_path
            )
            event = make_event(confidence=80)
            alerter.on_detection(event)

            lines = tmp_path.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["address"] == event.device.address
            assert entry["fingerprint_id"] == event.fingerprint.id
            assert entry["confidence"] == 80
            assert "timestamp" in entry
            assert "matched_fields" in entry
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_multiple_log_entries_appended(self) -> None:
        console, _ = make_console()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = pathlib.Path(tmp.name)

        try:
            alerter = Alerter(
                cooldown=0, sound=False, console=console, log_path=tmp_path
            )
            alerter.on_detection(make_event(address="AA:BB:CC:DD:EE:FF"))
            alerter.on_detection(make_event(address="11:22:33:44:55:66"))

            lines = tmp_path.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 2
            for line in lines:
                entry = json.loads(line)
                assert "address" in entry
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_suppressed_event_not_logged(self) -> None:
        console, _ = make_console()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = pathlib.Path(tmp.name)

        try:
            alerter = Alerter(
                cooldown=30, sound=False, console=console, log_path=tmp_path
            )
            alerter.on_detection(make_event())
            alerter.on_detection(make_event())  # suppressed

            lines = tmp_path.read_text(encoding="utf-8").strip().splitlines()
            # Only one log entry — the second was suppressed
            assert len(lines) == 1
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_no_log_when_log_path_is_none(self) -> None:
        """No file I/O should occur when log_path is None (no errors raised)."""
        console, _ = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console, log_path=None)
        event = make_event()
        # Should not raise
        result = alerter.on_detection(event)
        assert result is True

    def test_log_entry_has_correct_schema_fields(self) -> None:
        console, _ = make_console()
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as tmp:
            tmp_path = pathlib.Path(tmp.name)

        try:
            alerter = Alerter(
                cooldown=0, sound=False, console=console, log_path=tmp_path
            )
            alerter.on_detection(make_event(confidence=90, matched_fields=["name_pattern"]))

            entry = json.loads(tmp_path.read_text(encoding="utf-8").strip())
            expected_keys = {
                "timestamp",
                "address",
                "name",
                "fingerprint_id",
                "fingerprint_name",
                "vendor",
                "rssi",
                "rssi_threshold",
                "confidence",
                "confidence_percent",
                "matched_fields",
                "in_proximity",
            }
            assert expected_keys.issubset(entry.keys())
        finally:
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Alerter — console output
# ---------------------------------------------------------------------------

class TestAlerterConsoleOutput:
    """Tests for Rich console rendering."""

    def test_detection_card_printed_on_alert(self) -> None:
        console, buf = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        event = make_event()
        alerter.on_detection(event)
        output = buf.getvalue()
        assert len(output) > 0

    def test_detection_card_contains_device_name(self) -> None:
        console, buf = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        event = make_event()
        alerter.on_detection(event)
        output = buf.getvalue()
        assert event.fingerprint.name in output

    def test_detection_card_contains_address(self) -> None:
        console, buf = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        event = make_event(address="AA:BB:CC:DD:EE:FF")
        alerter.on_detection(event)
        output = buf.getvalue()
        assert "AA:BB:CC:DD:EE:FF" in output

    def test_detection_card_contains_rssi(self) -> None:
        console, buf = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        event = make_event(rssi=-62)
        alerter.on_detection(event)
        output = buf.getvalue()
        assert "-62" in output

    def test_detection_card_contains_vendor(self) -> None:
        console, buf = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        event = make_event()
        alerter.on_detection(event)
        output = buf.getvalue()
        assert event.fingerprint.vendor in output

    def test_detection_card_contains_matched_fields(self) -> None:
        console, buf = make_console()
        alerter = Alerter(cooldown=0, sound=False, console=console)
        event = make_event(matched_fields=["manufacturer_id", "name_pattern"])
        alerter.on_detection(event)
        output = buf.getvalue()
        assert "manufacturer_id" in output
        assert "name_pattern" in output

    def test_print_status_outputs_message(self) -> None:
        console, buf = make_console()
        alerter = Alerter(console=console)
        alerter.print_status("Scanning for devices…")
        assert "Scanning for devices" in buf.getvalue()

    def test_startup_banner_outputs_version(self) -> None:
        console, buf = make_console()
        alerter = Alerter(console=console)
        alerter.print_startup_banner(db_version="1.0.0", fingerprint_count=11)
        output = buf.getvalue()
        assert "1.0.0" in output
        assert "11" in output

    def test_print_device_list_outputs_fingerprint_id(self) -> None:
        console, buf = make_console()
        alerter = Alerter(console=console)
        fp = make_fingerprint(fp_id="meta-ray-ban-gen2")
        alerter.print_device_list([fp])
        output = buf.getvalue()
        assert "meta-ray-ban-gen2" in output

    def test_verbose_device_output(self) -> None:
        console, buf = make_console()
        alerter = Alerter(console=console)
        alerter.on_verbose_device(
            address="AA:BB:CC:DD:EE:FF",
            name="SomeDevice",
            rssi=-70,
            manufacturer_ids=[1234],
            service_uuids=["0000fe59-0000-1000-8000-00805f9b34fb"],
        )
        output = buf.getvalue()
        assert "AA:BB:CC:DD:EE:FF" in output
        assert "SomeDevice" in output

    def test_verbose_device_output_no_name(self) -> None:
        console, buf = make_console()
        alerter = Alerter(console=console)
        alerter.on_verbose_device(
            address="AA:BB:CC:DD:EE:FF",
            name=None,
            rssi=-75,
        )
        output = buf.getvalue()
        assert "AA:BB:CC:DD:EE:FF" in output
        # Should show placeholder for unknown name
        assert "unknown" in output.lower()


# ---------------------------------------------------------------------------
# RSSI bar rendering helpers
# ---------------------------------------------------------------------------

class TestRSSIBar:
    """Tests for _rssi_bar helper."""

    def test_returns_string(self) -> None:
        result = _rssi_bar(-65)
        assert isinstance(result, str)

    def test_bar_clamps_at_min(self) -> None:
        """Very weak RSSI should produce mostly empty bar."""
        result = _rssi_bar(-100)
        # The bar text (strip markup) should start with no filled blocks
        # At -100 dBm ratio=0 → all empty
        assert "█" not in result or result.count("█") == 0

    def test_bar_clamps_at_max(self) -> None:
        """Very strong RSSI should produce full bar."""
        result = _rssi_bar(-30)
        assert "░" not in result or result.count("░") == 0

    def test_bar_length_matches_width(self) -> None:
        """Total bar characters (filled + empty) should equal width."""
        for rssi in [-30, -50, -65, -80, -100]:
            bar = _rssi_bar(rssi, width=10)
            # Count the actual block characters ignoring Rich markup
            import re
            clean = re.sub(r"\[.*?\]", "", bar)
            assert len(clean) == 10, f"Bar length mismatch for RSSI {rssi}: '{clean}'"

    def test_stronger_rssi_more_filled(self) -> None:
        bar_strong = _rssi_bar(-40)
        bar_weak = _rssi_bar(-90)
        import re
        clean_strong = re.sub(r"\[.*?\]", "", bar_strong)
        clean_weak = re.sub(r"\[.*?\]", "", bar_weak)
        assert clean_strong.count("█") >= clean_weak.count("█")


# ---------------------------------------------------------------------------
# RSSI label helper
# ---------------------------------------------------------------------------

class TestRSSILabel:
    """Tests for _rssi_label helper."""

    def test_excellent_at_minus_40(self) -> None:
        label = _rssi_label(-40)
        assert "Excellent" in label

    def test_strong_at_minus_55(self) -> None:
        label = _rssi_label(-55)
        assert "Strong" in label

    def test_moderate_at_minus_65(self) -> None:
        label = _rssi_label(-65)
        assert "Moderate" in label

    def test_weak_at_minus_75(self) -> None:
        label = _rssi_label(-75)
        assert "Weak" in label

    def test_very_weak_at_minus_90(self) -> None:
        label = _rssi_label(-90)
        assert "Very Weak" in label


# ---------------------------------------------------------------------------
# RSSI colour helper
# ---------------------------------------------------------------------------

class TestRSSIColor:
    """Tests for _rssi_color helper."""

    def test_strong_signal_is_green(self) -> None:
        assert _rssi_color(-55) == "green"

    def test_moderate_signal_is_yellow(self) -> None:
        assert _rssi_color(-65) == "yellow"

    def test_weak_signal_is_orange(self) -> None:
        assert _rssi_color(-75) == "orange1"

    def test_very_weak_signal_is_red(self) -> None:
        assert _rssi_color(-90) == "red"


# ---------------------------------------------------------------------------
# Confidence bar / colour helpers
# ---------------------------------------------------------------------------

class TestConfidenceBar:
    """Tests for _confidence_bar helper."""

    def test_returns_string(self) -> None:
        assert isinstance(_confidence_bar(75.0), str)

    def test_100_percent_all_filled(self) -> None:
        bar = _confidence_bar(100.0, width=10)
        import re
        clean = re.sub(r"\[.*?\]", "", bar)
        assert clean.count("█") == 10
        assert clean.count("░") == 0

    def test_0_percent_all_empty(self) -> None:
        bar = _confidence_bar(0.0, width=10)
        import re
        clean = re.sub(r"\[.*?\]", "", bar)
        assert clean.count("█") == 0
        assert clean.count("░") == 10

    def test_bar_length_correct(self) -> None:
        import re
        for pct in [0.0, 25.0, 50.0, 75.0, 100.0]:
            bar = _confidence_bar(pct, width=10)
            clean = re.sub(r"\[.*?\]", "", bar)
            assert len(clean) == 10

    def test_clamps_above_100(self) -> None:
        bar = _confidence_bar(150.0, width=10)
        import re
        clean = re.sub(r"\[.*?\]", "", bar)
        assert clean.count("█") == 10

    def test_clamps_below_0(self) -> None:
        bar = _confidence_bar(-10.0, width=10)
        import re
        clean = re.sub(r"\[.*?\]", "", bar)
        assert clean.count("░") == 10


class TestConfidenceColor:
    """Tests for _confidence_color helper."""

    def test_high_confidence_green(self) -> None:
        assert "green" in _confidence_color(80.0)

    def test_medium_confidence_yellow(self) -> None:
        assert _confidence_color(60.0) == "yellow"

    def test_low_confidence_orange(self) -> None:
        assert _confidence_color(30.0) == "orange1"

    def test_exact_75_is_green(self) -> None:
        assert "green" in _confidence_color(75.0)

    def test_exact_50_is_yellow(self) -> None:
        assert _confidence_color(50.0) == "yellow"
