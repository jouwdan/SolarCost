"""Energy, costs and current tariff sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, ENERGY, METRICS, PERIODS

PERIOD_NAMES = {
    "today": "Today",
    "week": "This week",
    "month": "This month",
    "last_month": "Last month",
    "last_months": "Last {last_months} months",
    "year": "This year",
    "last_years": "Last {last_years} years",
    "all_time": "All time",
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            SolarCostSensor(entry.runtime_data, entry, period, metric)
            for period in PERIODS
            for metric in METRICS
        ]
        + [
            SolarCostSensor(entry.runtime_data, entry, "current", metric)
            for metric in ("import_rate", "export_rate")
        ]
    )


class SolarCostSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _attr_attribution = "Estimated by SolarCost"
    _unrecorded_attributes = frozenset(
        {"source_status", "meter_diagnostics", "updated_at", "time_of_use"}
    )

    def __init__(self, coordinator, entry, period, metric):
        super().__init__(coordinator)
        self.period, self.metric = period, metric
        self._attr_unique_id = f"{entry.entry_id}_{period}_{metric}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="SolarCost",
            model="Electricity bill estimator",
        )
        self._attr_translation_key = metric
        self._attr_translation_placeholders = (
            {"period": PERIOD_NAMES[period].format(**coordinator.settings)}
            if period != "current"
            else {}
        )
        self._attr_suggested_display_precision = 3 if metric in ENERGY else 2
        if period == "current":
            self._attr_native_unit_of_measurement = f"{coordinator.settings['currency']}/kWh"
            self._attr_suggested_display_precision = 4
            self._attr_icon = "mdi:cash-clock"
        else:
            self._attr_native_unit_of_measurement = (
                "kWh" if metric in ENERGY else coordinator.settings["currency"]
            )
            self._attr_device_class = (
                SensorDeviceClass.ENERGY if metric in ENERGY else SensorDeviceClass.MONETARY
            )
            if period == "all_time":
                self._attr_state_class = (
                    SensorStateClass.TOTAL_INCREASING
                    if metric in ENERGY
                    else SensorStateClass.TOTAL
                )

    @property
    def native_value(self):
        if self.period == "current":
            return self.coordinator.data["rates"][0 if self.metric == "import_rate" else 1]
        period = self.coordinator.data["periods"][self.period]
        return round(period[self.metric], 6) if period["has_history"] else None

    @property
    def extra_state_attributes(self):
        if self.period == "current":
            return {"time_zone": self.coordinator.settings["time_zone"]}
        data = self.coordinator.data
        period = data["periods"][self.period]
        attributes = {
            key: period[key] for key in ("start", "end", "partial_history", "has_history")
        }
        attributes["tracking_since"] = data["since"]
        if self.metric in ("import_cost", "net_cost"):
            attributes["time_of_use"] = period["time_of_use"]
            attributes["time_of_use_since"] = data["time_of_use_since"]
        if self.metric == "net_cost":
            attributes.update({key: round(period[key], 6) for key in METRICS if key != "net_cost"})
            attributes.update(
                {
                    "source_status": data["source_status"],
                    "meter_diagnostics": data["readings"],
                    "updated_at": data["updated_at"],
                    "estimated": True,
                }
            )
        return attributes
