"""Configure SolarCost with native Home Assistant forms."""

import re
from copy import deepcopy

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import DEFAULTS, DOMAIN, SOURCES
from .ledger import validate_tariff


def meter_schema(values):
    return vol.Schema(
        {
            vol.Required(
                f"{source}_sensor", default=values.get(f"{source}_sensor", vol.UNDEFINED)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            )
            for source in SOURCES
        }
    )


def meter_errors(hass, values):
    errors, entities = {}, []
    registry = er.async_get(hass)
    for source in SOURCES:
        field = f"{source}_sensor"
        entity = values[field]
        entities.append(entity)
        state, registered = hass.states.get(entity), registry.async_get(entity)
        if (
            state is None
            or state.attributes.get("unit_of_measurement") not in ("Wh", "kWh", "MWh")
            or state.attributes.get("state_class") not in ("total", "total_increasing")
            or (registered is not None and registered.platform == DOMAIN)
        ):
            errors[field] = "invalid_meter"
    if len(set(entities)) != len(entities):
        errors["base"] = "duplicate_meter"
    return errors


class TariffSteps:
    """The same tariff editor serves first-time setup and subsequent changes."""

    _options = False
    _editing = None

    async def async_step_rates(self, user_input=None):
        errors = {}
        if user_input is not None:
            candidate = {**self._settings, **user_input}
            try:
                validate_tariff(candidate)
            except (ValueError, TypeError):
                errors["base"] = "invalid_tariff"
            else:
                self._settings = candidate
                return await (self.async_step_init() if self._options else self.async_step_bands())
        fields = {}
        for key in (
            "import_rate",
            "export_rate",
            "standing_charge",
            "monthly_charge",
            "vat_percent",
            "discount_percent",
        ):
            field = vol.Required if key in ("import_rate", "export_rate") else vol.Optional
            fields[field(key, default=self._settings[key])] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    mode="box",
                    step="any",
                    **(
                        {"min": 0}
                        if key
                        in ("standing_charge", "monthly_charge", "vat_percent", "discount_percent")
                        else {}
                    ),
                    **({"max": 100} if key.endswith("percent") else {}),
                )
            )
        for key, maximum in (("last_months", 1200), ("last_years", 100)):
            fields[vol.Required(key, default=self._settings[key])] = vol.All(
                vol.Coerce(int), vol.Range(min=1, max=maximum)
            )
        return self.async_show_form(
            step_id="rates",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"currency": self._settings["currency"]},
        )

    async def async_step_bands(self, user_input=None):
        menu = ["add_band"]
        if self._settings["bands"]:
            menu += ["edit_band", "remove_band"]
        if self._options:
            menu += ["init"]
        menu += ["save"]
        summary = (
            "\n".join(
                f"• {band['name']}: {band['start'][:5]}–{band['end'][:5]}"
                for band in self._settings["bands"]
            )
            or "Base rates apply all day."
        )
        return self.async_show_menu(
            step_id="bands", menu_options=menu, description_placeholders={"bands": summary}
        )

    async def async_step_add_band(self, user_input=None):
        self._editing = None
        return await self.async_step_band()

    def _band_selector(self, multiple=False):
        return selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    {"value": str(index), "label": band["name"]}
                    for index, band in enumerate(self._settings["bands"])
                ],
                multiple=multiple,
                mode="dropdown",
            )
        )

    async def async_step_edit_band(self, user_input=None):
        if user_input is not None:
            self._editing = int(user_input["band"])
            return await self.async_step_band()
        return self.async_show_form(
            step_id="edit_band",
            data_schema=vol.Schema({vol.Required("band"): self._band_selector()}),
        )

    async def async_step_remove_band(self, user_input=None):
        if user_input is not None:
            self._settings["bands"] = [
                band
                for index, band in enumerate(self._settings["bands"])
                if str(index) not in user_input["bands"]
            ]
            return await self.async_step_bands()
        return self.async_show_form(
            step_id="remove_band",
            data_schema=vol.Schema({vol.Required("bands"): self._band_selector(multiple=True)}),
        )

    async def async_step_band(self, user_input=None):
        errors = {}
        defaults = {
            "name": "",
            "start": "23:00:00",
            "end": "07:00:00",
            "days": [str(day) for day in range(7)],
            "import_rate": self._settings["import_rate"],
            "export_rate": self._settings["export_rate"],
        }
        if self._editing is not None:
            defaults = self._settings["bands"][self._editing]
        if user_input is not None:
            user_input = {**user_input, "name": user_input["name"].strip()}
            bands = deepcopy(self._settings["bands"])
            if self._editing is None:
                bands.append(user_input)
            else:
                bands[self._editing] = user_input
            try:
                validate_tariff({**self._settings, "bands": bands})
            except (ValueError, TypeError):
                errors["base"] = "invalid_band"
                defaults = user_input
            else:
                self._settings["bands"] = bands
                return await self.async_step_bands()
        fields = {
            vol.Required("name", default=defaults["name"]): selector.TextSelector(),
            vol.Required("start", default=defaults["start"]): selector.TimeSelector(),
            vol.Required("end", default=defaults["end"]): selector.TimeSelector(),
            vol.Required("days", default=defaults["days"]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"value": str(index), "label": label}
                        for index, label in enumerate(
                            (
                                "Monday",
                                "Tuesday",
                                "Wednesday",
                                "Thursday",
                                "Friday",
                                "Saturday",
                                "Sunday",
                            )
                        )
                    ],
                    multiple=True,
                )
            ),
        }
        for key in ("import_rate", "export_rate"):
            fields[vol.Required(key, default=defaults[key])] = selector.NumberSelector(
                selector.NumberSelectorConfig(mode="box", step="any")
            )
        return self.async_show_form(
            step_id="band",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={"currency": self._settings["currency"]},
        )

    async def async_step_save(self, user_input=None):
        return self.async_create_entry(
            title="" if self._options else self._settings["name"], data=self._settings
        )


class SolarCostConfigFlow(TariffSteps, config_entries.ConfigFlow, domain=DOMAIN):
    """Choose four energy meters, then configure prices."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = meter_errors(self.hass, user_input)
            user_input["currency"] = user_input["currency"].strip().upper()
            if not re.fullmatch("[A-Z]{3}", user_input["currency"]):
                errors["currency"] = "invalid_currency"
            if not user_input["name"].strip():
                errors["name"] = "invalid_name"
            if not errors:
                self._settings = {
                    **deepcopy(DEFAULTS),
                    **user_input,
                    "time_zone": self.hass.config.time_zone,
                }
                return await self.async_step_rates()
        schema = {
            vol.Required("name", default="SolarCost"): selector.TextSelector(),
            vol.Required(
                "currency", default=self.hass.config.currency or "EUR"
            ): selector.TextSelector(),
            **meter_schema(user_input or {}).schema,
        }
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SolarCostOptionsFlow()


class SolarCostOptionsFlow(TariffSteps, config_entries.OptionsFlowWithReload):
    """Keep ledger currency/timezone fixed; changes affect future charges."""

    _options = True
    _settings = None

    async def async_step_init(self, user_input=None):
        if self._settings is None:
            self._settings = deepcopy({**self.config_entry.data, **self.config_entry.options})
        return self.async_show_menu(
            step_id="init", menu_options=["rates", "bands", "meters", "save"]
        )

    async def async_step_meters(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = meter_errors(self.hass, user_input)
            if not errors:
                self._settings.update(user_input)
                return await self.async_step_init()
        return self.async_show_form(
            step_id="meters", data_schema=meter_schema(self._settings), errors=errors
        )
