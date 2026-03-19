"""Constants for the RCA integration."""

DOMAIN = "rca"

# Configuration keys
CONF_PLATE = "plate"
CONF_SEARCH_TYPE = "search_type"
CONF_BROWSER_SERVICE_URL = "browser_service_url"
CONF_UPDATE_INTERVAL = "update_interval"
CONF_WARNING_DAYS = "warning_days"
CONF_ALERT_PRESET = "alert_preset"

# Defaults
DEFAULT_BROWSER_SERVICE_URL = "http://10.0.102.10:8194"
DEFAULT_UPDATE_INTERVAL = 86400  # 24 hours in seconds
MIN_UPDATE_INTERVAL = 3600  # 1 hour
MAX_UPDATE_INTERVAL = 604800  # 7 days
DEFAULT_WARNING_DAYS = 30

# Search types
SEARCH_TYPE_PLATE = "numar"
SEARCH_TYPE_VIN = "serie"

# Events
EVENT_RCA_EXPIRING_SOON = "rca_expiring_soon"

# Alert preset configuration
ALERT_PRESET_CONSERVATIVE = "conservative"
ALERT_PRESET_STANDARD = "standard"
ALERT_PRESET_MINIMAL = "minimal"
ALERT_PRESET_OFF = "off"

DEFAULT_ALERT_PRESET = ALERT_PRESET_STANDARD

ALERT_PRESETS = {
    ALERT_PRESET_CONSERVATIVE: {
        "thresholds": [60, 30, 14, 7],
        "daily_below": 7,
    },
    ALERT_PRESET_STANDARD: {
        "thresholds": [30, 14, 7],
        "daily_below": 7,
    },
    ALERT_PRESET_MINIMAL: {
        "thresholds": [7],
        "daily_below": 7,
    },
    ALERT_PRESET_OFF: {
        "thresholds": [],
        "daily_below": -1,
    },
}

# Attribution
ATTRIBUTION = "Data provided by AIDA (aida.info.ro)"
