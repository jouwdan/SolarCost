"""SolarCost: estimated electricity bills from local energy meters."""

import sqlite3
from datetime import date, datetime
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import SolarCostCoordinator

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    """Expose date-range billing reports, including when no entry is loaded."""
    integration = await async_get_integration(hass, DOMAIN)
    card_url = "/solarcost/solarcost-card.js"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(f"/solarcost/{name}", str(Path(__file__).parent / name), False)
            for name in ("solarcost-card.js", "solarcost-tou-card.js")
        ]
    )
    frontend.add_extra_js_url(hass, f"{card_url}?v={integration.version}")

    async def get_report(call):
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or not isinstance(getattr(entry, "runtime_data", None), SolarCostCoordinator)
        ):
            raise ServiceValidationError("Choose a loaded SolarCost integration")
        try:
            start, end = (
                date.fromisoformat(call.data["start"]),
                date.fromisoformat(call.data["end"]),
            )
            today = datetime.now(entry.runtime_data.ledger.zone).date()
            if end > today or end < start or (end - start).days > 36600:
                raise ValueError("Choose up to 100 years, ending today or earlier")
            return await hass.async_add_executor_job(
                entry.runtime_data.ledger.report, start, end, call.data["group_by"]
            )
        except ValueError as exc:
            raise ServiceValidationError(str(exc)) from exc
        except (OSError, sqlite3.Error) as exc:
            raise HomeAssistantError(f"Cannot read SolarCost ledger: {exc}") from exc

    hass.services.async_register(
        DOMAIN,
        "get_report",
        get_report,
        schema=vol.Schema(
            {
                vol.Required("config_entry_id"): cv.string,
                vol.Required("start"): cv.string,
                vol.Required("end"): cv.string,
                vol.Optional("group_by", default="month"): vol.In(("day", "week", "month", "year")),
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )
    return True


async def async_setup_entry(hass, entry):
    coordinator = SolarCostCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start()
    return True


async def async_unload_entry(hass, entry):
    await entry.runtime_data.async_refresh()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
