"""Bridge Home Assistant state updates to the local ledger."""

import logging
import sqlite3
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SOURCES
from .ledger import Ledger, finite_number

_LOGGER = logging.getLogger(__name__)


class SolarCostCoordinator(DataUpdateCoordinator):
    """All database work runs sequentially in Home Assistant's executor."""

    def __init__(self, hass, entry):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=timedelta(minutes=1)
        )
        self.settings = {**entry.data, **entry.options}
        self._pending = []
        self.ledger = Ledger(
            hass.config.path("solarcost", f"{entry.entry_id}.db"),
            self.settings["time_zone"],
            self.settings["currency"],
        )

    async def _async_setup(self):
        try:
            await self.hass.async_add_executor_job(
                self.ledger.initialize, dt_util.utcnow().timestamp()
            )
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise UpdateFailed(f"Cannot open SolarCost ledger: {exc}") from exc

    @callback
    def start(self):
        self.config_entry.async_on_unload(
            async_track_state_change_event(
                self.hass,
                [self.settings[f"{source}_sensor"] for source in SOURCES],
                self._state_changed,
            )
        )

    @callback
    def _state_changed(self, event):
        for source in SOURCES:
            if self.settings[f"{source}_sensor"] == event.data["entity_id"]:
                self._pending.append((source, event.data["new_state"]))
        self.config_entry.async_create_background_task(
            self.hass, self.async_request_refresh(), "SolarCost meter update"
        )

    async def _async_update_data(self):
        samples, status = [], {}
        now = dt_util.utcnow().timestamp()
        pending_count = len(self._pending)
        readings = self._pending[:pending_count] + [
            (source, self.hass.states.get(self.settings[f"{source}_sensor"])) for source in SOURCES
        ]
        for source, state in readings:
            entity = self.settings[f"{source}_sensor"]
            try:
                if (
                    state is None
                    or state.attributes.get("restored")
                    or state.attributes.get("state_class") not in ("total", "total_increasing")
                ):
                    raise ValueError("Missing cumulative energy sensor")
                factor = {"Wh": 0.001, "kWh": 1, "MWh": 1000}[
                    state.attributes.get("unit_of_measurement")
                ]
                value = finite_number(state.state, minimum=0) * factor
                last_reset = dt_util.parse_datetime(str(state.attributes.get("last_reset", "")))
                samples.append(
                    (
                        source,
                        {
                            "entity": entity,
                            "value": value,
                            "at": state.last_updated.timestamp(),
                            "resetting": state.attributes["state_class"] == "total_increasing",
                            "last_reset": dt_util.as_utc(last_reset).timestamp()
                            if last_reset
                            else None,
                        },
                    )
                )
                status[source] = "available"
            except (ValueError, TypeError, KeyError):
                status[source] = "unavailable_or_invalid"
        try:
            data = await self.hass.async_add_executor_job(self._update_ledger, samples, now, status)
            del self._pending[:pending_count]
            return data
        except (OSError, sqlite3.Error, ValueError) as exc:
            raise UpdateFailed(f"Cannot update SolarCost ledger: {exc}") from exc

    def _update_ledger(self, samples, now, status):
        self.ledger.update(self.settings, samples, now)
        data = self.ledger.snapshot(
            now, int(self.settings["last_months"]), int(self.settings["last_years"])
        )
        data["source_status"] = status
        return data
