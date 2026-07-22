"""RCA Insurance Check integration for Home Assistant."""
import logging
from datetime import date

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .alerts import RcaExpiryAlerts
from .const import (
    DOMAIN,
    SERVICE_CHECK_POLICY,
    ATTR_PLATE,
    ATTR_SEARCH_TYPE,
    ATTR_DATE,
    ATTR_REMEMBER,
    SEARCH_TYPE_PLATE,
    SEARCH_TYPE_VIN,
)
from .coordinator import RcaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

CHECK_POLICY_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PLATE): cv.string,
        vol.Optional(ATTR_SEARCH_TYPE): vol.In(
            [SEARCH_TYPE_PLATE, SEARCH_TYPE_VIN]
        ),
        vol.Optional(ATTR_DATE): cv.date,
        vol.Optional(ATTR_REMEMBER, default=True): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up from configuration.yaml (not used)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RCA from a config entry."""
    _LOGGER.debug("Setting up RCA entry %s", entry.entry_id)

    coordinator = RcaDataUpdateCoordinator(hass, entry)
    await coordinator.async_load_override()
    await coordinator.async_config_entry_first_refresh()

    # Set up expiry alerts
    alerts = RcaExpiryAlerts(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "alerts": alerts,
    }

    # Register alert listener after data is stored
    alerts.register(coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _async_register_services(hass)

    _LOGGER.debug("RCA setup complete for entry %s", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload RCA config entry."""
    _LOGGER.debug("Unloading RCA entry %s", entry.entry_id)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        if isinstance(data, dict):
            alerts = data.get("alerts")
            if alerts:
                alerts.unregister()

    # Remove the service once no config entries remain.
    if not [
        key
        for key, data in hass.data.get(DOMAIN, {}).items()
        if isinstance(data, dict) and "coordinator" in data
    ]:
        hass.services.async_remove(DOMAIN, SERVICE_CHECK_POLICY)
        hass.data.get(DOMAIN, {}).pop("_services_registered", None)

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update - reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services (once)."""
    if hass.data.get(DOMAIN, {}).get("_services_registered"):
        return

    async def _handle_check_policy(call: ServiceCall) -> ServiceResponse:
        """Check RCA validity for a plate on a given (optionally future) date."""
        coordinator = _resolve_coordinator(hass, call.data.get(ATTR_PLATE))

        plate = (call.data.get(ATTR_PLATE) or coordinator.plate).strip().upper()
        search_type = call.data.get(ATTR_SEARCH_TYPE) or coordinator.search_type
        ref_date: date | None = call.data.get(ATTR_DATE)
        remember = call.data.get(ATTR_REMEMBER, True)

        if ref_date is not None and ref_date < date.today():
            raise ServiceValidationError("date must be today or a future date")

        try:
            result = await coordinator.async_check_date(
                plate, search_type, ref_date
            )
        except Exception as err:  # noqa: BLE001 - surfaced to the caller
            raise HomeAssistantError(f"RCA check failed: {err}") from err

        # Remember a future-dated upcoming policy so daily polls reflect it
        # until the reference date is reached.
        if (
            remember
            and ref_date is not None
            and ref_date > date.today()
            and plate == coordinator.plate
            and result.get("has_policy")
        ):
            await coordinator.async_set_override(ref_date)
            await coordinator.async_request_refresh()

        return result

    hass.services.async_register(
        DOMAIN,
        SERVICE_CHECK_POLICY,
        _handle_check_policy,
        schema=CHECK_POLICY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.data.setdefault(DOMAIN, {})["_services_registered"] = True


def _resolve_coordinator(
    hass: HomeAssistant, plate: str | None
) -> RcaDataUpdateCoordinator:
    """Find the coordinator matching a plate, or the only one configured."""
    coordinators = [
        data["coordinator"]
        for data in hass.data.get(DOMAIN, {}).values()
        if isinstance(data, dict) and "coordinator" in data
    ]
    if not coordinators:
        raise ServiceValidationError("No RCA integration is configured")

    if plate:
        wanted = plate.strip().upper()
        for coordinator in coordinators:
            if coordinator.plate == wanted:
                return coordinator
        # Plate not configured — still allow an ad-hoc check via any coordinator.
        return coordinators[0]

    if len(coordinators) > 1:
        raise ServiceValidationError(
            "Multiple vehicles configured; specify the 'plate' field"
        )
    return coordinators[0]
