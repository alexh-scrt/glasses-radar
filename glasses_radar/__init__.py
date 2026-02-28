"""glasses_radar: Passive BLE scanner that detects smart glasses radio fingerprints.

This package provides tools to monitor nearby Bluetooth Low Energy (BLE)
advertisements and identify smart glasses devices such as Meta Ray-Bans,
Bose Frames, and TCL NXTWEAR glasses based on their radio fingerprints.

Typical usage::

    $ glasses-radar --rssi -65 --duration 60 --verbose
"""

__version__ = "0.1.0"
__author__ = "glasses-radar contributors"
__license__ = "MIT"

__all__ = ["__version__", "__author__", "__license__"]
