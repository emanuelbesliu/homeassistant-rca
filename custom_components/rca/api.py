"""API client for the rca-browser microservice."""
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 180  # 3 minutes — reCAPTCHA solving can take time


class RcaBrowserApiError(Exception):
    """Error from the rca-browser microservice."""


class RcaBrowserApi:
    """Async client for the rca-browser microservice."""

    def __init__(self, browser_service_url: str) -> None:
        """Initialize the API client."""
        self.base_url = browser_service_url.rstrip("/")

    async def check_rca(
        self,
        plate: str,
        search_type: str = "numar",
        date: str | None = None,
    ) -> dict[str, Any]:
        """Check RCA policy for a vehicle.

        Args:
            plate: Plate number or VIN.
            search_type: "numar" (plate) or "serie" (VIN).
            date: Date in dd.mm.yyyy format (defaults to today on server).

        Returns:
            Parsed response from the browser service.

        Raises:
            RcaBrowserApiError: On communication or server errors.
        """
        url = f"{self.base_url}/check-rca"
        payload: dict[str, str] = {
            "plate": plate,
            "search_type": search_type,
        }
        if date:
            payload["date"] = date

        _LOGGER.debug("Calling rca-browser: POST %s %s", url, payload)

        try:
            timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    data = await resp.json()

                    if resp.status != 200:
                        msg = data.get("message", f"HTTP {resp.status}")
                        raise RcaBrowserApiError(
                            f"rca-browser error: {msg}"
                        )

                    return data

        except aiohttp.ClientError as err:
            raise RcaBrowserApiError(
                f"Cannot connect to rca-browser at {self.base_url}: {err}"
            ) from err

    async def health_check(self) -> bool:
        """Check if the browser service is healthy.

        Returns:
            True if healthy, False otherwise.
        """
        url = f"{self.base_url}/health"
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("status") == "ok"
                    return False
        except Exception:
            return False
