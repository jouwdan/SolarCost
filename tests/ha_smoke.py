"""Real HA lifecycle check. Run inside the official Home Assistant container."""

import asyncio
import json
import logging
import tempfile
from datetime import datetime
from pathlib import Path

import yaml
from homeassistant import bootstrap, loader
from homeassistant.components import frontend
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component


async def block_flows(hass):
    """Exercise opt-in setup and options against real HA, without touching normal onboarding."""
    attributes = {
        "device_class": "energy",
        "state_class": "total_increasing",
        "unit_of_measurement": "kWh",
    }
    for source in ("solar", "usage", "import", "export"):
        hass.states.async_set(f"sensor.block_{source}", 0, attributes)
    flow = await hass.config_entries.flow.async_init("solarcost", context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "SolarCost Blocks",
            "currency": "LKR",
            **{
                f"{source}_sensor": f"sensor.block_{source}"
                for source in ("solar", "usage", "import", "export")
            },
        },
    )
    assert flow["step_id"] == "rates"
    assert not {"tariff_model", "categories", "enabled"} & {
        str(key) for key in flow["data_schema"].schema
    }
    rates = {
        "import_rate": 3,
        "export_rate": 2,
        "standing_charge": 0,
        "last_months": 12,
        "last_years": 5,
    }
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], rates)
    assert flow["step_id"] == "bands" and "save" in flow["menu_options"]
    minimal = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "save"}
    )
    assert minimal["type"] == "create_entry"
    assert minimal["result"].data["tariff_model"] == "flat"
    assert minimal["result"].data["block_categories"] == []
    await hass.async_block_till_done()
    assert minimal["result"].state.value == "loaded"
    assert await hass.config_entries.async_unload(minimal["result"].entry_id)
    flow = await hass.config_entries.flow.async_init("solarcost", context={"source": "user"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {
            "name": "SolarCost Tier Test",
            "currency": "LKR",
            **{
                f"{source}_sensor": f"sensor.block_{source}"
                for source in ("solar", "usage", "import", "export")
            },
        },
    )
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], rates)
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "blocks"}
    )
    assert flow["step_id"] == "blocks"
    schema = flow["data_schema"]
    categories_field = next(
        value for key, value in schema.schema.items() if str(key) == "categories"
    )
    serialized = categories_field.serialize()["selector"]["object"]
    assert (
        serialized["multiple"] and serialized["fields"]["blocks"]["selector"]["object"]["multiple"]
    )
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"enabled": True, "categories": []}
    )
    assert flow["errors"]["base"] == "invalid_blocks"
    categories = json.loads(
        (Path(__file__).resolve().parents[1] / "examples/block_tariff.json").read_text()
    )["block_categories"]
    invalid = json.loads(json.dumps(categories))
    invalid[0]["up_to"] = 0
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"enabled": True, "categories": invalid}
    )
    assert flow["errors"]["base"] == "invalid_blocks"
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"enabled": True, "categories": categories}
    )
    assert flow["step_id"] == "bands" and "add_band" not in flow["menu_options"]
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "save"})
    assert flow["type"] == "create_entry"
    entry = flow["result"]
    await hass.async_block_till_done()
    assert entry.state.value == "loaded"
    for quantity, cost in (
        (30, 150),
        (30.001, 150.009),
        (60, 420),
        (60.001, 840.02),
        (90, 1440),
        (120, 2280),
        (180, 4920),
        (180.001, 5850.1),
        (181, 5950),
    ):
        hass.states.async_set("sensor.block_import", quantity, attributes)
        await hass.async_block_till_done()
        await entry.runtime_data.async_refresh()
        result = entry.runtime_data.data["periods"]["month"]
        assert abs(result["import_cost"] - cost) < 1e-5, result
        assert (
            abs(sum(row["import_cost"] for row in result["time_of_use"].values()) - cost) < 1e-5
        ), result
    rate = hass.states.get("sensor.solarcost_tier_test_current_import_rate")
    assert float(rate.state) == 100 and rate.attributes["category"] == "High consumption", rate
    assert rate.attributes["monthly_import_kwh"] == 181, rate
    today = datetime.now(entry.runtime_data.ledger.zone).date().isoformat()
    report = await hass.services.async_call(
        "solarcost",
        "get_report",
        {"config_entry_id": entry.entry_id, "start": today, "end": today},
        blocking=True,
        return_response=True,
    )
    assert abs(report["totals"]["import_cost"] - 5950) < 1e-5, report
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert abs(entry.runtime_data.data["periods"]["month"]["import_cost"] - 5950) < 1e-5
    options = await hass.config_entries.options.async_init(entry.entry_id)
    assert "bands" not in options["menu_options"]
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {"next_step_id": "blocks"}
    )
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {"enabled": False, "categories": categories}
    )
    assert "bands" in options["menu_options"]
    options = await hass.config_entries.options.async_configure(
        options["flow_id"], {"next_step_id": "save"}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.data["tariff_model"] == "flat"
    # End the unreported interval spanning the tariff change before measuring the new rate.
    hass.states.async_set("sensor.block_import", 181, {**attributes, "test_marker": "flat"})
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    hass.states.async_set("sensor.block_import", 182, attributes)
    await hass.async_block_till_done()
    await entry.runtime_data.async_refresh()
    assert abs(entry.runtime_data.data["periods"]["month"]["import_cost"] - 5953) < 1e-5, (
        entry.runtime_data.data
    )
    assert entry.options["block_categories"] == categories
    assert await hass.config_entries.async_unload(entry.entry_id)
    print(
        "PASS: unchanged basic onboarding, native nested block selectors, invalid schedules, opt-in setup, threshold repricing, block sensors, reports, reload and disabling with history retained"
    )


async def main():
    logging.basicConfig(level=logging.WARNING)
    with tempfile.TemporaryDirectory() as directory:
        Path(directory, "custom_components").symlink_to(
            Path(__file__).resolve().parents[1] / "custom_components"
        )
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        power_package = yaml.safe_load(
            (Path(directory) / "custom_components/solarcost/power_meters.yaml").read_text()
        )
        try:
            assert await bootstrap.async_from_config_dict(
                {
                    **power_package,
                    "homeassistant": {
                        "time_zone": "Europe/Dublin",
                        "currency": "EUR",
                        "country": "IE",
                        "latitude": 0,
                        "longitude": 0,
                        "elevation": 0,
                        "unit_system": "metric",
                    },
                },
                hass,
            )
            assert await async_setup_component(hass, "solarcost", {})
            assert any(
                url.startswith("/solarcost/solarcost-card.js?v=")
                for url in hass.data[frontend.DATA_EXTRA_MODULE_URL].urls
            )
            await hass.async_start()
            power_attributes = {
                "device_class": "power",
                "state_class": "measurement",
                "unit_of_measurement": "W",
            }
            for source, value in (("solar", 3600), ("home", 600), ("grid", -1200)):
                hass.states.async_set(f"sensor.{source}_power", value, power_attributes)
            await hass.async_block_till_done()
            assert float(hass.states.get("sensor.solarcost_import_power").state) == 0
            assert float(hass.states.get("sensor.solarcost_export_power").state) == 1200
            await asyncio.sleep(0.1)
            for source, value in (("solar", 3601), ("home", 601), ("grid", -1201)):
                hass.states.async_set(f"sensor.{source}_power", value, power_attributes)
            await hass.async_block_till_done()
            for source in ("solar", "usage", "export"):
                meter = hass.states.get(f"sensor.solarcost_{source}_meter")
                assert 0 < float(meter.state) < 1, meter
                assert meter.attributes["unit_of_measurement"] == "kWh", meter
                assert meter.attributes["state_class"] == "total", meter
            hass.states.async_set("sensor.grid_power", 1200, power_attributes)
            await hass.async_block_till_done()
            assert float(hass.states.get("sensor.solarcost_import_power").state) == 1200
            assert float(hass.states.get("sensor.solarcost_export_power").state) == 0
            await asyncio.sleep(0.1)
            hass.states.async_set("sensor.grid_power", 1201, power_attributes)
            await hass.async_block_till_done()
            assert 0 < float(hass.states.get("sensor.solarcost_import_meter").state) < 1
            hass.states.async_set("sensor.grid_power", "unavailable", power_attributes)
            await hass.async_block_till_done()
            for source in ("import", "export"):
                assert hass.states.get(f"sensor.solarcost_{source}_power").state == "unavailable"
            attributes = {
                "device_class": "energy",
                "state_class": "total_increasing",
                "unit_of_measurement": "kWh",
            }
            for source in ("solar", "usage", "import", "export"):
                hass.states.async_set(f"sensor.test_{source}", 100, attributes)
            flow = await hass.config_entries.flow.async_init(
                "solarcost", context={"source": "user"}
            )
            assert flow["step_id"] == "user", flow
            setup = {
                "name": "SolarCost Test",
                "currency": "EUR",
                **{
                    f"{source}_sensor": f"sensor.test_{source}"
                    for source in ("solar", "usage", "import", "export")
                },
            }
            flow = await hass.config_entries.flow.async_configure(flow["flow_id"], setup)
            assert flow["step_id"] == "rates", flow
            rates = {
                "import_rate": 0.3,
                "export_rate": 0.15,
                "standing_charge": 0.6,
                "last_months": 12,
                "last_years": 5,
            }
            flow = await hass.config_entries.flow.async_configure(flow["flow_id"], rates)
            assert flow["step_id"] == "bands", flow
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"], {"next_step_id": "add_band"}
            )
            assert flow["step_id"] == "band", flow
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"],
                {
                    "name": "Night",
                    "start": "23:00:00",
                    "end": "07:00:00",
                    "days": [str(day) for day in range(7)],
                    "import_rate": 0.3,
                    "export_rate": 0.15,
                },
            )
            assert flow["step_id"] == "bands", flow
            flow = await hass.config_entries.flow.async_configure(
                flow["flow_id"], {"next_step_id": "save"}
            )
            assert flow["type"] == "create_entry", flow
            entry = flow["result"]
            assert entry.data["tariff_model"] == "flat" and not entry.data["block_categories"]
            await hass.async_block_till_done()
            assert entry.state.value == "loaded", entry.state
            coordinator = entry.runtime_data
            for source, value in (("solar", 112), ("usage", 118), ("import", 110), ("export", 104)):
                hass.states.async_set(f"sensor.test_{source}", value, attributes)
            await hass.async_block_till_done()
            await coordinator.async_refresh()
            result = coordinator.data["periods"]["all_time"]
            assert abs(result["import_cost"] - 3) < 1e-6, result
            assert abs(result["export_credit"] - 0.6) < 1e-6, result
            entities = [
                state
                for state in hass.states.async_all("sensor")
                if state.entity_id.startswith("sensor.solarcost_test_")
            ]
            assert len(entities) == 74, [
                (state.entity_id, state.state) for state in hass.states.async_all("sensor")
            ]
            for state in entities:
                if state.entity_id.startswith("sensor.solarcost_test_last_month_"):
                    assert state.state == "unknown", state
                    assert state.attributes["has_history"] is False, state
                else:
                    assert state.state not in ("unknown", "unavailable"), state
            assert any(
                state.attributes.get("friendly_name") == "SolarCost Test Today estimated bill"
                for state in entities
            ), entities[0]
            today = datetime.now(coordinator.ledger.zone).date().isoformat()
            report = await hass.services.async_call(
                "solarcost",
                "get_report",
                {"config_entry_id": entry.entry_id, "start": today, "end": today},
                blocking=True,
                return_response=True,
            )
            assert report["totals"]["import_kwh"] == 10, report
            assert (
                abs(sum(band["import_cost"] for band in report["time_of_use"].values()) - 3) < 1e-6
            )
            imported = hass.states.get("sensor.solarcost_test_today_import_cost")
            assert imported.attributes["time_of_use"] == report["time_of_use"], imported
            assert imported.attributes["time_of_use_since"], imported
            dashboard = yaml.safe_load(
                (Path(directory) / "custom_components/solarcost/card.yaml").read_text()
            )
            assert dashboard["type"] == "custom:solarcost-card"
            entity = dashboard["entity"].replace("sensor.solarcost_", "sensor.solarcost_test_")
            bill = hass.states.get(entity)
            assert bill.attributes["entry_id"] == entry.entry_id
            assert bill.attributes["period"] == "month"
            assert bill.attributes["metric"] == "net_cost"
            assert bill.attributes["period_name"] == "This month"
            assert bill.attributes["time_of_use"]
            current = hass.states.get("sensor.solarcost_test_current_import_rate")
            assert current.attributes["entry_id"] == entry.entry_id
            assert current.attributes["metric"] == "import_rate"
            assert current.attributes["time_zone"] == "Europe/Dublin"
            # Two rapid updates must preserve the intermediate reset, despite debouncing.
            hass.states.async_set("sensor.test_import", 0, attributes)
            hass.states.async_set("sensor.test_import", 115, attributes)
            await hass.async_block_till_done()
            await coordinator.async_refresh()
            assert coordinator.data["periods"]["all_time"]["import_kwh"] == 125
            assert await hass.config_entries.async_reload(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.runtime_data.data["periods"]["all_time"]["import_kwh"] == 125
            options = await hass.config_entries.options.async_init(entry.entry_id)
            options = await hass.config_entries.options.async_configure(
                options["flow_id"], {"next_step_id": "rates"}
            )
            options = await hass.config_entries.options.async_configure(
                options["flow_id"], {**rates, "import_rate": 0.4, "last_months": 6}
            )
            options = await hass.config_entries.options.async_configure(
                options["flow_id"], {"next_step_id": "save"}
            )
            await hass.async_block_till_done()
            assert entry.options["import_rate"] == 0.4
            assert entry.runtime_data.data["rates"][0] == 0.4
            assert entry.runtime_data.data["periods"]["all_time"]["import_kwh"] == 125
            assert await hass.config_entries.async_unload(entry.entry_id)
            await block_flows(hass)
            print(
                "PASS: fractional power helpers, grid direction, setup, 74 translated sensors, last-month history, time-band costs and card template, reports, queued reset, reload persistence, options and unload"
            )
        finally:
            await hass.async_stop()


asyncio.run(main())
