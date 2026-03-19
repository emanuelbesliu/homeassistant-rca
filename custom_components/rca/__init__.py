"""RCA Insurance Check integration for Home Assistant."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .alerts import RcaExpiryAlerts
from .const import DOMAIN
from .coordinator import RcaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up from configuration.yaml (not used)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RCA from a config entry."""
    _LOGGER.debug("Setting up RCA entry %s", entry.entry_id)

    coordinator = RcaDataUpdateCoordinator(hass, entry)
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

    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Handle options update - reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)
