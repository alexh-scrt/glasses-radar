"""Async BLE scanner for glasses_radar.

This module implements the passive Bluetooth Low Energy advertisement scanner
using the Bleak library.  It continuously listens for BLE advertisement
packets, converts them into :class:`~glasses_radar.models.BLEDeviceSnapshot`
objects, feeds them through the :class:`~glasses_radar.matcher.FingerprintMatcher`,
and dispatches any detection events to the :class:`~glasses_radar.alerter.Alerter`.

Typical usage::

    import asyncio
    from glasses_radar.fingerprints import FingerprintDatabase
    from glasses_radar.matcher import FingerprintMatcher
    from glasses_radar.alerter import Alerter
    from glasses_radar.scanner import BLEScanner

    db = FingerprintDatabase.load()
    matcher = FingerprintMatcher(db)
    alerter = Alerter()

    scanner = BLEScanner(matcher=matcher, alerter=alerter)
    asyncio.run(scanner.run(duration=60))
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import sys
from typing import Callable, Coroutine, Any

try:
    from bleak import BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
except ImportError as _bleak_import_error:  # pragma: no cover
    raise ImportError(
        "bleak is required for BLE scanning. "
        "Install it with: pip install bleak>=0.21.1"
    ) from _bleak_import_error

from glasses_radar.alerter import Alerter
from glasses_radar.matcher import FingerprintMatcher
from glasses_radar.models import BLEDeviceSnapshot

logger = logging.getLogger(__name__)

# Type alias for the optional callback invoked for every BLE advertisement,
# regardless of whether it matches a fingerprint.
VerboseCallback = Callable[[BLEDeviceSnapshot], None]


class ScannerError(Exception):
    """Raised when the BLE scanner encounters an unrecoverable error."""


class BLEScanner:
    """Passive BLE advertisement scanner that feeds the matcher/alerter pipeline.

    The scanner uses :class:`bleak.BleakScanner` in passive scanning mode to
    receive BLE advertisement packets without pairing or connecting to any
    device.  For every received advertisement it:

    1. Converts the raw Bleak data into a
       :class:`~glasses_radar.models.BLEDeviceSnapshot`.
    2. Passes the snapshot to the
       :class:`~glasses_radar.matcher.FingerprintMatcher`.
    3. For each :class:`~glasses_radar.models.DetectionEvent` returned,
       calls :meth:`~glasses_radar.alerter.Alerter.on_detection`.
    4. Optionally calls a *verbose_callback* with the snapshot (used to
       display all BLE devices in verbose mode).

    Attributes:
        matcher: The :class:`~glasses_radar.matcher.FingerprintMatcher` used
            to identify known smart glasses.
        alerter: The :class:`~glasses_radar.alerter.Alerter` responsible for
            rendering alerts and managing cooldowns.
        verbose: When ``True``, every received BLE advertisement is passed to
            :meth:`~glasses_radar.alerter.Alerter.on_verbose_device` in
            addition to the normal matching pipeline.
        rssi_threshold: Optional global RSSI floor (dBm).  Advertisements
            with an RSSI strictly below this value are silently ignored before
            matching.  ``None`` disables pre-filtering (all advertisements are
            processed).
        scan_interval: How often (in seconds) a brief log message is emitted
            showing the scanner is still running.  Set to ``0`` to disable.
    """

    def __init__(
        self,
        matcher: FingerprintMatcher,
        alerter: Alerter,
        verbose: bool = False,
        rssi_threshold: int | None = None,
        scan_interval: float = 10.0,
        verbose_callback: VerboseCallback | None = None,
    ) -> None:
        """Initialise the scanner.

        Args:
            matcher: A configured
                :class:`~glasses_radar.matcher.FingerprintMatcher` instance.
            alerter: A configured :class:`~glasses_radar.alerter.Alerter`
                instance.
            verbose: When ``True``, every BLE advertisement is surfaced to
                the alerter's verbose output in addition to normal fingerprint
                matching.
            rssi_threshold: Minimum RSSI (dBm) required to process an
                advertisement.  Advertisements weaker than this value are
                dropped before fingerprint matching.  ``None`` = no filtering.
            scan_interval: Seconds between periodic "still scanning" log
                messages.  ``0`` disables these messages.
            verbose_callback: Optional callable that receives every
                :class:`~glasses_radar.models.BLEDeviceSnapshot` regardless
                of fingerprint match outcome.  Useful for testing / custom
                integrations.
        """
        self.matcher = matcher
        self.alerter = alerter
        self.verbose = verbose
        self.rssi_threshold = rssi_threshold
        self.scan_interval = scan_interval
        self._verbose_callback = verbose_callback

        # Scanning statistics, reset on each call to run().
        self._advertisements_seen: int = 0
        self._advertisements_matched: int = 0
        self._advertisements_skipped_rssi: int = 0
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def advertisements_seen(self) -> int:
        """Total number of BLE advertisements received in the current/last scan."""
        return self._advertisements_seen

    @property
    def advertisements_matched(self) -> int:
        """Number of advertisements that produced at least one detection event."""
        return self._advertisements_matched

    @property
    def advertisements_skipped_rssi(self) -> int:
        """Number of advertisements dropped due to RSSI pre-filtering."""
        return self._advertisements_skipped_rssi

    @property
    def is_running(self) -> bool:
        """``True`` while :meth:`run` is executing."""
        return self._running

    async def run(
        self,
        duration: float = 0.0,
        on_start: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        """Start passive BLE scanning and block until complete.

        Args:
            duration: How long to scan in seconds.  ``0`` (the default) means
                scan indefinitely until :meth:`stop` is called or the process
                receives ``SIGINT`` / ``KeyboardInterrupt``.
            on_start: Optional zero-argument callback invoked immediately after
                the BleakScanner starts.  Useful for printing startup messages.
            on_stop: Optional zero-argument callback invoked after scanning
                finishes (whether by timeout, :meth:`stop`, or exception).

        Raises:
            ScannerError: If the BLE adapter is unavailable or Bleak raises
                an unexpected error during scanner setup.
        """
        self._reset_stats()
        self._running = True
        self._stop_event: asyncio.Event = asyncio.Event()

        logger.info(
            "Starting BLE scanner (duration=%s s, rssi_threshold=%s dBm, verbose=%s)",
            duration if duration > 0 else "∞",
            self.rssi_threshold,
            self.verbose,
        )

        scanner = BleakScanner(
            detection_callback=self._detection_callback,
        )

        try:
            await scanner.start()
        except Exception as exc:
            self._running = False
            raise ScannerError(
                f"Failed to start BLE scanner: {exc}.  "
                f"Ensure Bluetooth is enabled and you have the required permissions."
            ) from exc

        if on_start is not None:
            try:
                on_start()
            except Exception:  # pragma: no cover
                logger.exception("on_start callback raised an exception")

        try:
            await self._scan_loop(duration=duration)
        except asyncio.CancelledError:
            logger.info("BLE scanner task was cancelled.")
        except KeyboardInterrupt:  # pragma: no cover
            logger.info("BLE scanner interrupted by user (KeyboardInterrupt).")
        finally:
            self._running = False
            try:
                await scanner.stop()
            except Exception as exc:  # pragma: no cover
                logger.warning("Error stopping BLE scanner: %s", exc)

            if on_stop is not None:
                try:
                    on_stop()
                except Exception:  # pragma: no cover
                    logger.exception("on_stop callback raised an exception")

            logger.info(
                "BLE scanner stopped. Seen=%d, Matched=%d, SkippedRSSI=%d",
                self._advertisements_seen,
                self._advertisements_matched,
                self._advertisements_skipped_rssi,
            )

    def stop(self) -> None:
        """Signal the scanner to stop after the current scan cycle.

        This method is safe to call from a synchronous context (e.g. a signal
        handler).  The scanner will stop gracefully after the event loop
        processes the stop signal.
        """
        if hasattr(self, "_stop_event"):
            self._stop_event.set()
            logger.debug("Stop signal sent to BLE scanner.")

    def get_stats(self) -> dict[str, int]:
        """Return a snapshot of scanner statistics.

        Returns:
            Dictionary with keys:
            - ``advertisements_seen``: Total advertisements received.
            - ``advertisements_matched``: Advertisements matching a fingerprint.
            - ``advertisements_skipped_rssi``: Advertisements dropped by RSSI
              filter.
        """
        return {
            "advertisements_seen": self._advertisements_seen,
            "advertisements_matched": self._advertisements_matched,
            "advertisements_skipped_rssi": self._advertisements_skipped_rssi,
        }

    # ------------------------------------------------------------------
    # Internal implementation
    # ------------------------------------------------------------------

    def _reset_stats(self) -> None:
        """Reset all scanner statistics to zero."""
        self._advertisements_seen = 0
        self._advertisements_matched = 0
        self._advertisements_skipped_rssi = 0

    async def _scan_loop(self, duration: float) -> None:
        """Wait for the scan to complete or be stopped.

        Args:
            duration: Total scan duration in seconds.  ``0`` = indefinite.
        """
        if duration > 0:
            # Wait until duration expires or stop is requested, whichever comes first.
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=duration,
                )
            except asyncio.TimeoutError:
                # Normal completion — duration elapsed.
                logger.debug("Scan duration of %.1f s elapsed.", duration)
        else:
            # Indefinite scan: wait for stop signal only.
            if self.scan_interval > 0:
                await self._indefinite_scan_with_heartbeat()
            else:
                await self._stop_event.wait()

    async def _indefinite_scan_with_heartbeat(self) -> None:
        """Run indefinitely, emitting a periodic heartbeat log message."""
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.scan_interval,
                )
                # Stop event was set — exit loop.
                break
            except asyncio.TimeoutError:
                logger.debug(
                    "Still scanning… seen=%d matched=%d",
                    self._advertisements_seen,
                    self._advertisements_matched,
                )

    def _detection_callback(
        self, device: BLEDevice, advertisement_data: AdvertisementData
    ) -> None:
        """Bleak detection callback invoked for every received BLE advertisement.

        This method is called from within the asyncio event loop by Bleak.
        It is intentionally synchronous to match Bleak's callback signature;
        any I/O within this method must be non-blocking.

        Args:
            device: The :class:`bleak.backends.device.BLEDevice` object from
                Bleak, containing the MAC address and name.
            advertisement_data: The
                :class:`bleak.backends.scanner.AdvertisementData` containing
                manufacturer data, service UUIDs, TX power, etc.
        """
        self._advertisements_seen += 1

        # Extract RSSI — prefer the advertisement RSSI, fall back to device RSSI.
        rssi: int = (
            advertisement_data.rssi
            if advertisement_data.rssi is not None
            else (device.rssi if hasattr(device, "rssi") and device.rssi is not None else -127)
        )

        # RSSI pre-filter: drop advertisements below the configured floor.
        if self.rssi_threshold is not None and rssi < self.rssi_threshold:
            self._advertisements_skipped_rssi += 1
            logger.debug(
                "Dropping advertisement from %s (RSSI %d dBm < threshold %d dBm)",
                device.address,
                rssi,
                self.rssi_threshold,
            )
            return

        snapshot = self._build_snapshot(device, advertisement_data, rssi)

        # Verbose mode: surface every advertisement to the alerter.
        if self.verbose:
            try:
                self.alerter.on_verbose_device(
                    address=snapshot.address,
                    name=snapshot.name,
                    rssi=snapshot.rssi,
                    manufacturer_ids=snapshot.manufacturer_ids or None,
                    service_uuids=snapshot.service_uuids or None,
                )
            except Exception:  # pragma: no cover
                logger.exception(
                    "Alerter.on_verbose_device raised an exception for %s",
                    snapshot.address,
                )

        # Custom verbose callback (for testing / integration).
        if self._verbose_callback is not None:
            try:
                self._verbose_callback(snapshot)
            except Exception:  # pragma: no cover
                logger.exception(
                    "verbose_callback raised an exception for %s", snapshot.address
                )

        # Fingerprint matching.
        try:
            events = self.matcher.match(snapshot)
        except Exception:  # pragma: no cover
            logger.exception(
                "FingerprintMatcher.match raised an exception for %s",
                snapshot.address,
            )
            return

        if events:
            self._advertisements_matched += 1
            for event in events:
                try:
                    self.alerter.on_detection(event)
                except Exception:  # pragma: no cover
                    logger.exception(
                        "Alerter.on_detection raised an exception for event from %s",
                        snapshot.address,
                    )

    @staticmethod
    def _build_snapshot(
        device: BLEDevice,
        advertisement_data: AdvertisementData,
        rssi: int,
    ) -> BLEDeviceSnapshot:
        """Convert Bleak device + advertisement data into a BLEDeviceSnapshot.

        Args:
            device: Bleak :class:`~bleak.backends.device.BLEDevice`.
            advertisement_data: Bleak
                :class:`~bleak.backends.scanner.AdvertisementData`.
            rssi: Pre-computed RSSI value (dBm).

        Returns:
            A populated :class:`~glasses_radar.models.BLEDeviceSnapshot`.
        """
        # Prefer the local name from advertisement_data over the device name,
        # as the former is fresher and comes directly from the packet.
        name: str | None = (
            advertisement_data.local_name
            or (device.name if device.name else None)
        )

        # Convert manufacturer data: Bleak uses {int: bytes} already.
        manufacturer_data: dict[int, bytes] = dict(
            advertisement_data.manufacturer_data or {}
        )

        # Service UUIDs: Bleak returns a list of UUID strings.
        service_uuids: list[str] = list(advertisement_data.service_uuids or [])

        # Service data: Bleak uses {str: bytes}.
        service_data: dict[str, bytes] = dict(
            advertisement_data.service_data or {}
        )

        # TX Power.
        tx_power: int | None = advertisement_data.tx_power

        return BLEDeviceSnapshot(
            address=device.address,
            name=name,
            rssi=rssi,
            manufacturer_data=manufacturer_data,
            service_uuids=service_uuids,
            service_data=service_data,
            tx_power=tx_power,
            timestamp=datetime.datetime.now(tz=datetime.timezone.utc),
        )


def build_scanner(
    matcher: FingerprintMatcher,
    alerter: Alerter,
    *,
    verbose: bool = False,
    rssi_threshold: int | None = None,
    scan_interval: float = 10.0,
    verbose_callback: VerboseCallback | None = None,
) -> BLEScanner:
    """Convenience factory for constructing a :class:`BLEScanner`.

    This function exists as a simple entry-point for the CLI to construct a
    scanner without importing implementation details.

    Args:
        matcher: Configured :class:`~glasses_radar.matcher.FingerprintMatcher`.
        alerter: Configured :class:`~glasses_radar.alerter.Alerter`.
        verbose: Enable verbose mode (show all BLE devices).
        rssi_threshold: RSSI pre-filter threshold in dBm, or ``None``.
        scan_interval: Heartbeat log interval in seconds (``0`` to disable).
        verbose_callback: Optional callback for every advertisement.

    Returns:
        A configured :class:`BLEScanner` ready to call :meth:`~BLEScanner.run`.
    """
    return BLEScanner(
        matcher=matcher,
        alerter=alerter,
        verbose=verbose,
        rssi_threshold=rssi_threshold,
        scan_interval=scan_interval,
        verbose_callback=verbose_callback,
    )
