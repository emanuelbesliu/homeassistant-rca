"""Data Update Coordinator for RCA Insurance Check."""

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .api import RcaBrowserApi, RcaBrowserApiError
from .const import (
    DOMAIN,
    CONF_PLATE,
    CONF_SEARCH_TYPE,
    CONF_BROWSER_SERVICE_URL,
    CONF_UPDATE_INTERVAL,
    CONF_WARNING_DAYS,
    DEFAULT_BROWSER_SERVICE_URL,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_WARNING_DAYS,
    SEARCH_TYPE_PLATE,
    EVENT_RCA_EXPIRING_SOON,
)

_LOGGER = logging.getLogger(__name__)


class RcaDataUpdateCoordinator(DataUpdateCoordinator):
    """Coordinator for fetching RCA insurance data via the browser microservice."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.entry = entry

        self.plate: str = entry.data[CONF_PLATE]
        self.search_type: str = entry.data.get(CONF_SEARCH_TYPE, SEARCH_TYPE_PLATE)

        browser_url = entry.options.get(
            CONF_BROWSER_SERVICE_URL,
            entry.data.get(CONF_BROWSER_SERVICE_URL, DEFAULT_BROWSER_SERVICE_URL),
        )
        self.api = RcaBrowserApi(browser_url)

        update_interval = entry.options.get(
            CONF_UPDATE_INTERVAL,
            entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
        )

        self._warning_days: int = entry.options.get(
            CONF_WARNING_DAYS,
            entry.data.get(CONF_WARNING_DAYS, DEFAULT_WARNING_DAYS),
        )

        # Track whether we already fired the expiring-soon event this cycle
        self._last_event_fired_for: str | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.plate}",
            update_interval=timedelta(seconds=update_interval),
        )

    @property
    def warning_days(self) -> int:
        """Return the current expiry warning threshold."""
        return self._warning_days

    @warning_days.setter
    def warning_days(self, value: int) -> None:
        """Set a new expiry warning threshold."""
        self._warning_days = value

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch RCA data from the browser microservice."""
        try:
            raw = await self.api.check_rca(
                plate=self.plate,
                search_type=self.search_type,
            )
        except RcaBrowserApiError as err:
            raise UpdateFailed(f"rca-browser error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

        parsed = self._parse_response(raw)

        # Fire expiry warning event if applicable
        self._check_expiry_warning(parsed)

        return parsed

    def _parse_response(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Parse raw browser service response into a clean data dict.

        Expected raw format:
        {
            "status": "ok",
            "has_policy": true/false,
            "ocr_details": {
                "valid_from": "DD.MM.YYYY",
                "valid_to": "DD.MM.YYYY",
                "insurer": "..."
            }
        }
        """
        now = datetime.now()

        has_policy = raw.get("has_policy", False)
        ocr = raw.get("ocr_details") or {}

        valid_from_str = ocr.get("valid_from")
        valid_to_str = ocr.get("valid_to")
        insurer = ocr.get("insurer")

        valid_from = None
        valid_to = None
        days_remaining = 0

        if valid_from_str:
            try:
                valid_from = datetime.strptime(valid_from_str, "%d.%m.%Y").date()
            except ValueError:
                _LOGGER.warning("Could not parse valid_from: %s", valid_from_str)

        if valid_to_str:
            try:
                valid_to = datetime.strptime(valid_to_str, "%d.%m.%Y").date()
                days_remaining = max(0, (valid_to - now.date()).days)
            except ValueError:
                _LOGGER.warning("Could not parse valid_to: %s", valid_to_str)

        return {
            "has_policy": has_policy,
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "insurer": insurer,
            "days_remaining": days_remaining,
            "plate": self.plate,
            "last_update": now.isoformat(),
        }

    def _check_expiry_warning(self, data: dict[str, Any]) -> None:
        """Fire an event if the RCA policy is expiring within the warning threshold."""
        if not data.get("has_policy"):
            return

        days = data.get("days_remaining", 0)
        expiry_key = data.get("valid_to", "")

        if days <= self._warning_days and expiry_key:
            # Only fire once per expiry date to avoid spamming
            if self._last_event_fired_for != expiry_key:
                self._last_event_fired_for = expiry_key
                self.hass.bus.async_fire(
                    EVENT_RCA_EXPIRING_SOON,
                    {
                        "plate": self.plate,
                        "days_remaining": days,
                        "valid_to": expiry_key,
                        "insurer": data.get("insurer"),
                    },
                )
                _LOGGER.info(
                    "RCA for %s expiring in %d days (threshold: %d)",
                    self.plate,
                    days,
                    self._warning_days,
                )
