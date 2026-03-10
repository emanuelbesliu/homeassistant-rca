"""Sensor entities for RCA Insurance Check."""

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ATTRIBUTION, CONF_PLATE
from .coordinator import RcaDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up RCA sensors from a config entry."""
    coordinator: RcaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    plate = entry.data[CONF_PLATE]

    entities = [
        RcaHasPolicySensor(coordinator, entry, plate),
        RcaValidFromSensor(coordinator, entry, plate),
        RcaValidToSensor(coordinator, entry, plate),
        RcaInsurerSensor(coordinator, entry, plate),
        RcaDaysRemainingSensor(coordinator, entry, plate),
    ]

    async_add_entities(entities)
    _LOGGER.info("Created %d RCA sensors for %s", len(entities), plate)


class RcaBaseSensor(CoordinatorEntity, SensorEntity):
    """Base sensor for RCA Insurance Check."""

    _attr_attribution = ATTRIBUTION

    def __init__(
        self,
        coordinator: RcaDataUpdateCoordinator,
        entry: ConfigEntry,
        plate: str,
    ) -> None:
        """Initialize base sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._plate = plate

    @property
    def device_info(self) -> dict[str, Any]:
        """Device info — groups all sensors under one device per vehicle."""
        return {
            "identifiers": {(DOMAIN, self._plate)},
            "name": f"RCA {self._plate}",
            "manufacturer": "AIDA Romania",
            "model": "RCA Insurance Check",
            "entry_type": "service",
        }


class RcaHasPolicySensor(RcaBaseSensor):
    """Sensor indicating whether the vehicle has a valid RCA policy."""

    _attr_icon = "mdi:shield-car"

    def __init__(
        self,
        coordinator: RcaDataUpdateCoordinator,
        entry: ConfigEntry,
        plate: str,
    ) -> None:
        """Initialize has_policy sensor."""
        super().__init__(coordinator, entry, plate)
        self._attr_name = f"RCA {plate} Has Policy"
        self._attr_unique_id = f"{DOMAIN}_{plate}_has_policy"

    @property
    def native_value(self) -> str | None:
        """Return 'Yes' or 'No' based on policy status."""
        if self.coordinator.data is None:
            return None
        has_policy = self.coordinator.data.get("has_policy")
        if has_policy is None:
            return None
        return "Yes" if has_policy else "No"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional attributes."""
        data = self.coordinator.data or {}
        return {
            "plate": self._plate,
            "insurer": data.get("insurer"),
            "last_update": data.get("last_update"),
        }


class RcaValidFromSensor(RcaBaseSensor):
    """Sensor for the RCA policy start date."""

    _attr_icon = "mdi:calendar-start"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(
        self,
        coordinator: RcaDataUpdateCoordinator,
        entry: ConfigEntry,
        plate: str,
    ) -> None:
        """Initialize valid_from sensor."""
        super().__init__(coordinator, entry, plate)
        self._attr_name = f"RCA {plate} Valid From"
        self._attr_unique_id = f"{DOMAIN}_{plate}_valid_from"

    @property
    def native_value(self) -> date | None:
        """Return the policy start date."""
        data = self.coordinator.data or {}
        valid_from_str = data.get("valid_from")
        if not valid_from_str:
            return None
        try:
            return date.fromisoformat(valid_from_str)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional attributes."""
        data = self.coordinator.data or {}
        return {
            "plate": self._plate,
            "has_policy": data.get("has_policy", False),
        }


class RcaValidToSensor(RcaBaseSensor):
    """Sensor for the RCA policy end date."""

    _attr_icon = "mdi:calendar-end"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(
        self,
        coordinator: RcaDataUpdateCoordinator,
        entry: ConfigEntry,
        plate: str,
    ) -> None:
        """Initialize valid_to sensor."""
        super().__init__(coordinator, entry, plate)
        self._attr_name = f"RCA {plate} Valid To"
        self._attr_unique_id = f"{DOMAIN}_{plate}_valid_to"

    @property
    def native_value(self) -> date | None:
        """Return the policy end date."""
        data = self.coordinator.data or {}
        valid_to_str = data.get("valid_to")
        if not valid_to_str:
            return None
        try:
            return date.fromisoformat(valid_to_str)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional attributes."""
        data = self.coordinator.data or {}
        return {
            "plate": self._plate,
            "has_policy": data.get("has_policy", False),
            "days_remaining": data.get("days_remaining", 0),
        }


class RcaInsurerSensor(RcaBaseSensor):
    """Sensor for the RCA insurer name."""

    _attr_icon = "mdi:office-building"

    def __init__(
        self,
        coordinator: RcaDataUpdateCoordinator,
        entry: ConfigEntry,
        plate: str,
    ) -> None:
        """Initialize insurer sensor."""
        super().__init__(coordinator, entry, plate)
        self._attr_name = f"RCA {plate} Insurer"
        self._attr_unique_id = f"{DOMAIN}_{plate}_insurer"

    @property
    def native_value(self) -> str | None:
        """Return the insurer name."""
        data = self.coordinator.data or {}
        return data.get("insurer") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional attributes."""
        data = self.coordinator.data or {}
        return {
            "plate": self._plate,
            "has_policy": data.get("has_policy", False),
            "valid_from": data.get("valid_from"),
            "valid_to": data.get("valid_to"),
        }


class RcaDaysRemainingSensor(RcaBaseSensor):
    """Sensor for days remaining until RCA policy expires."""

    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "days"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: RcaDataUpdateCoordinator,
        entry: ConfigEntry,
        plate: str,
    ) -> None:
        """Initialize days remaining sensor."""
        super().__init__(coordinator, entry, plate)
        self._attr_name = f"RCA {plate} Days Remaining"
        self._attr_unique_id = f"{DOMAIN}_{plate}_days_remaining"

    @property
    def native_value(self) -> int | None:
        """Return the number of days remaining."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("days_remaining", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Additional attributes."""
        data = self.coordinator.data or {}
        return {
            "plate": self._plate,
            "has_policy": data.get("has_policy", False),
            "valid_to": data.get("valid_to"),
            "insurer": data.get("insurer"),
            "last_update": data.get("last_update"),
        }
