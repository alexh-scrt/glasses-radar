"""Rich-powered alerter for glasses_radar detections.

This module is responsible for presenting detection events to the user via the
Rich terminal library.  It handles:

- Printing formatted detection cards when smart glasses are detected.
- Per-device alert cooldown to prevent repeated alerts for the same device.
- Optional terminal bell (audible beep) on detection.
- Optional structured JSON log output.
- A live scanning status line shown while no matches are active.

Typical usage::

    from glasses_radar.alerter import Alerter

    alerter = Alerter(rssi_threshold=-70, cooldown=30, sound=True)
    alerter.on_detection(event)
    alerter.print_status("Scanning…")
"""

from __future__ import annotations

import datetime
import json
import logging
import pathlib
import sys
from typing import TextIO

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

from glasses_radar.models import DetectionEvent

logger = logging.getLogger(__name__)

# RSSI bar rendering constants
_BAR_FULL = "█"
_BAR_EMPTY = "░"
_BAR_WIDTH = 10


class Alerter:
    """Manages detection alerts, cooldown tracking, and Rich terminal output.

    Attributes:
        rssi_threshold: Global RSSI threshold used to determine proximity
            (dBm).  When ``None``, per-fingerprint thresholds are used.
        cooldown: Seconds to suppress repeated alerts for the same device
            address.  Set to ``0`` to disable cooldown.
        sound: When ``True``, emit a terminal bell character on each alert.
        log_path: Optional filesystem path to append JSON log lines to.
        console: The Rich :class:`rich.console.Console` instance used for
            output.
    """

    def __init__(
        self,
        rssi_threshold: int | None = None,
        cooldown: int = 30,
        sound: bool = True,
        log_path: str | pathlib.Path | None = None,
        console: Console | None = None,
    ) -> None:
        """Initialise the alerter.

        Args:
            rssi_threshold: Global RSSI threshold override (dBm).  When
                provided this overrides per-fingerprint thresholds for
                proximity classification in alert output.  Does not change
                matching behaviour.
            cooldown: Seconds between repeated alerts for the same device
                address.  Defaults to 30 seconds.  Set to 0 to always alert.
            sound: Whether to emit an audible terminal bell (``\\a``) when an
                alert fires.  Defaults to ``True``.
            log_path: Optional path to a file where JSON-line detection events
                will be appended.  The file is created if it does not exist.
            console: Optional pre-constructed Rich :class:`~rich.console.Console`
                instance.  When ``None`` a default stderr console is created.
        """
        self.rssi_threshold = rssi_threshold
        self.cooldown = cooldown
        self.sound = sound
        self.log_path = pathlib.Path(log_path) if log_path is not None else None
        self.console: Console = console if console is not None else Console(stderr=False)

        # Map of device_address -> UTC datetime of last alert
        self._last_alert: dict[str, datetime.datetime] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_detection(self, event: DetectionEvent) -> bool:
        """Handle a detection event, alerting the user if cooldown allows.

        Args:
            event: The :class:`~glasses_radar.models.DetectionEvent` produced
                by the matcher.

        Returns:
            ``True`` if an alert was actually emitted (cooldown was not active),
            ``False`` if the event was suppressed by the cooldown.
        """
        address = event.device.address

        if self._is_in_cooldown(address):
            logger.debug(
                "Alert for %s suppressed (cooldown active)", address
            )
            return False

        # Update cooldown timestamp before rendering to avoid any race.
        self._last_alert[address] = datetime.datetime.now(tz=datetime.timezone.utc)
        event.alerted = True

        self._render_detection_card(event)

        if self.sound:
            self._emit_beep()

        if self.log_path is not None:
            self._append_log_entry(event)

        return True

    def on_verbose_device(
        self,
        address: str,
        name: str | None,
        rssi: int,
        manufacturer_ids: list[int] | None = None,
        service_uuids: list[str] | None = None,
    ) -> None:
        """Print a compact one-line entry for any BLE device (verbose mode).

        Args:
            address: BLE MAC address string.
            name: Advertised device name, or ``None``.
            rssi: Received signal strength in dBm.
            manufacturer_ids: List of manufacturer IDs seen in the
                advertisement, or ``None``.
            service_uuids: List of service UUIDs, or ``None``.
        """
        rssi_bar = _rssi_bar(rssi)
        display_name = name if name else "[dim]<unknown>[/dim]"
        mids = (
            ", ".join(str(m) for m in manufacturer_ids)
            if manufacturer_ids
            else ""
        )
        uuids_short = (
            ", ".join(u[:8] + "…" for u in service_uuids[:3])
            if service_uuids
            else ""
        )
        extra = " | ".join(filter(None, [mids, uuids_short]))
        self.console.print(
            f"  [dim]{address}[/dim]  {display_name}  "
            f"{rssi_bar} [dim]{rssi} dBm[/dim]"
            + (f"  [dim]{extra}[/dim]" if extra else "")
        )

    def print_status(self, message: str) -> None:
        """Print a transient status message (overwritten on next output).

        Args:
            message: Plain text status string.
        """
        self.console.print(f"[dim]{message}[/dim]")

    def print_startup_banner(self, db_version: str, fingerprint_count: int) -> None:
        """Print the startup banner showing database info.

        Args:
            db_version: Version string of the loaded fingerprint database.
            fingerprint_count: Number of enabled fingerprints loaded.
        """
        banner = Text()
        banner.append("glasses-radar", style="bold cyan")
        banner.append(" — Passive BLE Smart Glasses Detector\n", style="white")
        banner.append(f"  Fingerprint DB v{db_version} ", style="dim")
        banner.append(f"({fingerprint_count} signatures loaded)", style="dim")
        if self.rssi_threshold is not None:
            banner.append(
                f"  |  RSSI threshold: {self.rssi_threshold} dBm", style="dim"
            )
        banner.append(f"  |  Cooldown: {self.cooldown}s", style="dim")
        self.console.print(Panel(banner, box=box.ROUNDED, border_style="cyan"))

    def print_device_list(self, fingerprints: list) -> None:  # type: ignore[type-arg]
        """Print a Rich table listing all known fingerprinted devices.

        Args:
            fingerprints: List of :class:`~glasses_radar.models.Fingerprint`
                objects to display.
        """
        table = Table(
            title="Known Smart Glasses Fingerprints",
            box=box.ROUNDED,
            header_style="bold cyan",
            show_lines=True,
        )
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Device Name", style="bold white")
        table.add_column("Vendor", style="cyan")
        table.add_column("Mfr IDs", justify="right")
        table.add_column("Threshold", justify="right")
        table.add_column("Enabled", justify="center")

        for fp in fingerprints:
            enabled_str = "✅" if fp.enabled else "❌"
            mids = ", ".join(str(m) for m in fp.manufacturer_ids) or "—"
            table.add_row(
                fp.id,
                fp.name,
                fp.vendor,
                mids,
                f"{fp.rssi_threshold} dBm",
                enabled_str,
            )

        self.console.print(table)

    def reset_cooldown(self, address: str) -> None:
        """Manually clear the cooldown for a specific device address.

        Args:
            address: BLE MAC address string (case-insensitive).
        """
        self._last_alert.pop(address.upper(), None)

    def reset_all_cooldowns(self) -> None:
        """Clear all active cooldowns."""
        self._last_alert.clear()

    def is_in_cooldown(self, address: str) -> bool:
        """Check whether a device address is currently in cooldown.

        Args:
            address: BLE MAC address string.

        Returns:
            ``True`` if the device is in cooldown and should not be alerted.
        """
        return self._is_in_cooldown(address)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_in_cooldown(self, address: str) -> bool:
        """Return ``True`` if *address* has been alerted within the cooldown window."""
        if self.cooldown <= 0:
            return False
        last = self._last_alert.get(address)
        if last is None:
            return False
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        elapsed = (now - last).total_seconds()
        return elapsed < self.cooldown

    def _render_detection_card(self, event: DetectionEvent) -> None:
        """Render a Rich detection card to the console.

        Args:
            event: The matched :class:`~glasses_radar.models.DetectionEvent`.
        """
        fp = event.fingerprint
        device = event.device

        # Determine effective RSSI threshold for display.
        effective_threshold = (
            self.rssi_threshold
            if self.rssi_threshold is not None
            else fp.rssi_threshold
        )
        in_proximity = device.rssi >= effective_threshold
        proximity_label = (
            "[bold green]IN RANGE[/bold green]"
            if in_proximity
            else "[yellow]DISTANT[/yellow]"
        )

        # Build RSSI bar and label.
        rssi_bar = _rssi_bar(device.rssi)
        rssi_strength = _rssi_label(device.rssi)

        # Confidence bar.
        conf_pct = event.confidence_percent
        conf_bar = _confidence_bar(conf_pct)
        conf_color = _confidence_color(conf_pct)

        # Matched fields list.
        matched_str = ", ".join(event.matched_fields) if event.matched_fields else "—"

        # Timestamp.
        ts = event.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

        # Build the panel content as a table for alignment.
        grid = Table.grid(padding=(0, 1))
        grid.add_column(style="dim", justify="right", min_width=14)
        grid.add_column()

        grid.add_row("Device:", f"[bold white]{fp.name}[/bold white]")
        grid.add_row("Vendor:", f"[cyan]{fp.vendor}[/cyan]")
        grid.add_row("Address:", f"[dim]{device.address}[/dim]")
        if device.name:
            grid.add_row("BLE Name:", f"[dim]{device.name}[/dim]")
        grid.add_row(
            "RSSI:",
            f"{rssi_bar} [bold]{device.rssi} dBm[/bold]  {rssi_strength}",
        )
        grid.add_row("Proximity:", proximity_label)
        grid.add_row(
            "Confidence:",
            f"{conf_bar} [{conf_color}]{conf_pct:.1f}%[/{conf_color}]",
        )
        grid.add_row("Matched:", f"[italic]{matched_str}[/italic]")
        grid.add_row("Time:", f"[dim]{ts}[/dim]")

        title = Text()
        title.append("🚨 SMART GLASSES DETECTED", style="bold red")

        self.console.print()
        self.console.print(
            Panel(
                grid,
                title=title,
                border_style="red",
                box=box.HEAVY,
                expand=False,
            )
        )
        self.console.print()

    def _emit_beep(self) -> None:
        """Emit an audible terminal bell character to stdout."""
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except OSError:
            pass

    def _append_log_entry(self, event: DetectionEvent) -> None:
        """Append a JSON-line entry for the detection event to the log file.

        Args:
            event: The :class:`~glasses_radar.models.DetectionEvent` to log.
        """
        assert self.log_path is not None  # guaranteed by caller
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False))
                fh.write("\n")
        except OSError as exc:
            logger.error("Failed to write to log file '%s': %s", self.log_path, exc)


# ---------------------------------------------------------------------------
# RSSI / Confidence rendering helpers
# ---------------------------------------------------------------------------

def _rssi_bar(rssi: int, width: int = _BAR_WIDTH) -> str:
    """Return a Unicode block progress bar representing RSSI signal strength.

    The bar is based on a practical RSSI range of -100 dBm (worst) to -30 dBm
    (best), clamped to that range.

    Args:
        rssi: RSSI value in dBm (typically negative).
        width: Number of bar characters in the output.

    Returns:
        A string such as ``"████████░░"`` coloured with Rich markup.
    """
    rssi_min = -100
    rssi_max = -30
    clamped = max(rssi_min, min(rssi_max, rssi))
    ratio = (clamped - rssi_min) / (rssi_max - rssi_min)
    filled = round(ratio * width)
    empty = width - filled

    color = _rssi_color(rssi)
    bar = _BAR_FULL * filled + _BAR_EMPTY * empty
    return f"[{color}]{bar}[/{color}]"


def _rssi_label(rssi: int) -> str:
    """Return a human-readable signal strength label for a given RSSI value.

    Args:
        rssi: RSSI value in dBm.

    Returns:
        One of ``"Excellent"``, ``"Strong"``, ``"Moderate"``, ``"Weak"``, or
        ``"Very Weak"``.
    """
    if rssi >= -50:
        return "[bold green]Excellent[/bold green]"
    if rssi >= -60:
        return "[green]Strong[/green]"
    if rssi >= -70:
        return "[yellow]Moderate[/yellow]"
    if rssi >= -80:
        return "[orange1]Weak[/orange1]"
    return "[red]Very Weak[/red]"


def _rssi_color(rssi: int) -> str:
    """Return a Rich colour name for an RSSI value.

    Args:
        rssi: RSSI value in dBm.

    Returns:
        A Rich-compatible colour name string.
    """
    if rssi >= -60:
        return "green"
    if rssi >= -70:
        return "yellow"
    if rssi >= -80:
        return "orange1"
    return "red"


def _confidence_bar(percent: float, width: int = _BAR_WIDTH) -> str:
    """Return a Unicode block bar representing a confidence percentage.

    Args:
        percent: Confidence percentage in the range 0.0–100.0.
        width: Number of bar characters.

    Returns:
        A Rich-markup coloured progress bar string.
    """
    clamped = max(0.0, min(100.0, percent))
    filled = round(clamped / 100.0 * width)
    empty = width - filled
    color = _confidence_color(percent)
    bar = _BAR_FULL * filled + _BAR_EMPTY * empty
    return f"[{color}]{bar}[/{color}]"


def _confidence_color(percent: float) -> str:
    """Return a Rich colour name for a confidence percentage.

    Args:
        percent: Confidence percentage in the range 0.0–100.0.

    Returns:
        A Rich-compatible colour name string.
    """
    if percent >= 75:
        return "bold green"
    if percent >= 50:
        return "yellow"
    return "orange1"
