"""Data Update Coordinator for RCA Insurance Check."""

import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
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
    DEFAULT_BROWSER_SERVICE_URL,
    DEFAULT_UPDATE_INTERVAL,
    SEARCH_TYPE_PLATE,
    STORAGE_VERSION,
    STORAGE_KEY_OVERRIDE,
    AIDA_DATE_FORMAT,
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

        # Future-date override: when the plate has an upcoming (not-yet-active)
        # policy, polls for "today" return no policy. We remember the future
        # reference date so every poll checks that date until it is reached.
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_{STORAGE_KEY_OVERRIDE}"
        )
        self.override_date: date | None = None

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{self.plate}",
            update_interval=timedelta(seconds=update_interval),
        )

    async def async_load_override(self) -> None:
        """Load a persisted future-date override from storage."""
        stored = await self._store.async_load()
        if stored and stored.get("override_date"):
            try:
                self.override_date = datetime.strptime(
                    stored["override_date"], AIDA_DATE_FORMAT
                ).date()
                _LOGGER.debug(
                    "Loaded future-date override for %s: %s",
                    self.plate,
                    self.override_date,
                )
            except ValueError:
                self.override_date = None

    async def async_set_override(self, ref_date: date) -> None:
        """Persist a future-date override and refresh."""
        self.override_date = ref_date
        await self._store.async_save(
            {"override_date": ref_date.strftime(AIDA_DATE_FORMAT)}
        )
        _LOGGER.info(
            "Future-date override set for %s until %s", self.plate, ref_date
        )

    async def async_clear_override(self) -> None:
        """Clear any persisted future-date override."""
        if self.override_date is None:
            return
        self.override_date = None
        await self._store.async_remove()
        _LOGGER.info("Future-date override cleared for %s", self.plate)

    async def async_check_date(
        self, plate: str, search_type: str, ref_date: date | None
    ) -> dict[str, Any]:
        """Run a one-off check for an explicit date and return parsed data.

        Used by the check_policy service. Does not touch the poll state.
        """
        date_str = (
            ref_date.strftime(AIDA_DATE_FORMAT)
            if ref_date and ref_date != date.today()
            else None
        )
        raw = await self.api.check_rca(
            plate=plate, search_type=search_type, date=date_str
        )
        return self._parse_response(raw, ref_date or date.today())

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch RCA data from the browser microservice.

        Strategy: check "today" first. If a future-date override is active
        (set via the check_policy service) and today has not yet reached it,
        check that future date instead so sensors reflect the upcoming policy.
        Once today passes the override date, revert to normal today-based polls.
        """
        today = date.today()

        # If today has already reached/passed the override, drop it.
        if self.override_date is not None and today >= self.override_date:
            await self.async_clear_override()

        use_override = self.override_date is not None and today < self.override_date
        ref_date = self.override_date if use_override else today

        try:
            parsed = await self.async_check_date(
                self.plate, self.search_type, ref_date
            )
        except RcaBrowserApiError as err:
            raise UpdateFailed(f"rca-browser error: {err}") from err
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

        # An override that no longer yields a policy is stale — clear it and
        # fall back to a plain today check so sensors reflect reality.
        if use_override and not parsed.get("has_policy"):
            await self.async_clear_override()
            try:
                parsed = await self.async_check_date(
                    self.plate, self.search_type, today
                )
            except (RcaBrowserApiError, Exception) as err:
                raise UpdateFailed(f"rca-browser error: {err}") from err

        return parsed

    def _parse_response(
        self, raw: dict[str, Any], reference_date: date
    ) -> dict[str, Any]:
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
                days_remaining = (valid_to - now.date()).days
            except ValueError:
                _LOGGER.warning("Could not parse valid_to: %s", valid_to_str)

        # A policy whose start date is still in the future is not active today.
        starts_in_future = valid_from is not None and valid_from > now.date()

        return {
            "has_policy": has_policy,
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "insurer": insurer,
            "days_remaining": days_remaining,
            "plate": self.plate,
            "reference_date": reference_date.isoformat(),
            "starts_in_future": starts_in_future,
            "last_update": now.isoformat(),
        }
