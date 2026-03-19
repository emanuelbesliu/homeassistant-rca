"""Expiry alert system for the RCA integration.

Fires HA events and creates persistent notifications when an RCA policy
is approaching expiry or has expired.

Alert presets:
- Conservative: 60, 30, 14, 7 days + daily from 7 days
- Standard: 30, 14, 7 days + daily from 7 days
- Minimal: 7 days + daily from 7 days
- Off: no alerts
"""

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DOMAIN,
    ALERT_PRESETS,
    ALERT_PRESET_OFF,
    CONF_ALERT_PRESET,
    CONF_PLATE,
    DEFAULT_ALERT_PRESET,
    EVENT_RCA_EXPIRING_SOON,
)

_LOGGER = logging.getLogger(__name__)


class RcaExpiryAlerts:
    """Manages expiry alerts for a single RCA policy entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the alert manager."""
        self._hass = hass
        self._entry = entry
        self._fired_thresholds: set[int] = set()
        self._last_daily_alert_date: str | None = None
        self._unsub: Any = None

    def _get_preset_config(self) -> dict[str, Any]:
        """Get the active alert preset configuration from entry options."""
        preset_key = self._entry.options.get(
            CONF_ALERT_PRESET,
            self._entry.data.get(CONF_ALERT_PRESET, DEFAULT_ALERT_PRESET),
        )
        return ALERT_PRESETS.get(preset_key, ALERT_PRESETS[DEFAULT_ALERT_PRESET])

    @property
    def _plate(self) -> str:
        """Return the vehicle plate number."""
        return self._entry.data.get(CONF_PLATE, "")

    def register(self, coordinator: DataUpdateCoordinator) -> None:
        """Register listener on coordinator updates."""
        self._unsub = coordinator.async_add_listener(self._on_update)

    def unregister(self) -> None:
        """Unregister listener."""
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _on_update(self) -> None:
        """Handle coordinator data update — check if alerts are needed."""
        preset_key = self._entry.options.get(
            CONF_ALERT_PRESET,
            self._entry.data.get(CONF_ALERT_PRESET, DEFAULT_ALERT_PRESET),
        )
        if preset_key == ALERT_PRESET_OFF:
            return

        coordinator = self._hass.data.get(DOMAIN, {}).get(
            self._entry.entry_id, {}
        )
        if isinstance(coordinator, dict):
            coordinator = coordinator.get("coordinator")
        if not coordinator or not hasattr(coordinator, "data") or not coordinator.data:
            return

        data = coordinator.data
        if not data.get("has_policy", False):
            return

        days_left = data.get("days_remaining")
        if days_left is None:
            return

        self._check_alerts(days_left)

    def _check_alerts(self, days_left: int) -> None:
        """Determine if an alert should be sent based on days_left and preset."""
        preset = self._get_preset_config()
        thresholds = preset["thresholds"]
        daily_below = preset["daily_below"]
        today = datetime.now().strftime("%Y-%m-%d")

        # Check milestone thresholds
        for threshold in thresholds:
            if (
                days_left <= threshold
                and threshold not in self._fired_thresholds
            ):
                self._fired_thresholds.add(threshold)
                if days_left > daily_below:
                    self._send_alert(days_left)
                    return

        # Daily alerts: at or below daily_below days, and when expired
        if daily_below >= 0 and days_left <= daily_below:
            if self._last_daily_alert_date != today:
                self._last_daily_alert_date = today
                self._send_alert(days_left)

    def _send_alert(self, days_left: int) -> None:
        """Fire event and create persistent notification."""
        plate = self._plate

        if days_left < 0:
            abs_days = abs(days_left)
            title = f"RCA Expired — {plate}"
            message = (
                f"Asigurarea RCA pentru {plate} a expirat "
                f"acum {abs_days} {'zi' if abs_days == 1 else 'zile'}. "
                f"Reînnoiți urgent!"
            )
            severity = "expired"
        elif days_left == 0:
            title = f"RCA Expires Today — {plate}"
            message = (
                f"Asigurarea RCA pentru {plate} expiră astăzi! "
                f"Reînnoiți urgent!"
            )
            severity = "expires_today"
        else:
            title = f"RCA Expiring — {plate}"
            message = (
                f"Asigurarea RCA pentru {plate} expiră în "
                f"{days_left} {'zi' if days_left == 1 else 'zile'}. "
                f"Reînnoiți polița."
            )
            severity = "expiring_soon"

        # Fire HA event (same event name for backward compatibility)
        event_data = {
            "entry_id": self._entry.entry_id,
            "plate": plate,
            "days_remaining": days_left,
            "severity": severity,
            "title": title,
            "message": message,
        }

        # Include insurer/valid_to if available from coordinator data
        coordinator = self._hass.data.get(DOMAIN, {}).get(
            self._entry.entry_id, {}
        )
        if isinstance(coordinator, dict):
            coordinator = coordinator.get("coordinator")
        if coordinator and hasattr(coordinator, "data") and coordinator.data:
            event_data["valid_to"] = coordinator.data.get("valid_to")
            event_data["insurer"] = coordinator.data.get("insurer")

        self._hass.bus.async_fire(EVENT_RCA_EXPIRING_SOON, event_data)
        _LOGGER.info(
            "RCA expiry alert fired: %s (%d days left)", plate, days_left
        )

        # Create persistent notification
        notification_id = f"rca_expiry_{self._entry.entry_id}"
        self._hass.components.persistent_notification.async_create(
            message=message,
            title=title,
            notification_id=notification_id,
        )
