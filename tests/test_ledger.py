"""Run with python -m unittest discover -s tests -v (stdlib only)."""

import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Load the pure accounting module without importing Home Assistant's entry point.
ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType("solarcost_accounting")
package.__path__ = [str(ROOT / "custom_components/solarcost")]
sys.modules[package.__name__] = package
from solarcost_accounting.ledger import Ledger, period_starts, rates_at, validate_tariff


def timestamp(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def sample(source, value, at, resetting=True, entity=None, last_reset=None):
    return source, {
        "entity": entity or f"sensor.{source}",
        "value": value,
        "at": at,
        "resetting": resetting,
        "last_reset": last_reset,
    }


BASE = {"import_rate": 0.30, "export_rate": 0.15, "standing_charge": 0.60, "bands": []}


class AccountingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(Path(self.temp.name) / "bill.db", "Europe/Dublin", "EUR")
        self.start = timestamp("2026-01-01T00:00:00")
        self.ledger.initialize(self.start)
        self.ledger.update(
            BASE,
            [sample(s, 100, self.start) for s in ("solar", "usage", "import", "export")],
            self.start,
        )

    def total(self, now):
        return self.ledger.snapshot(now, 12, 5)["periods"]["all_time"]

    def test_bill_persistence_and_duplicate_readings(self):
        end = self.start + 86400
        samples = [
            sample(s, value, end)
            for s, value in (("solar", 112), ("usage", 118), ("import", 110), ("export", 104))
        ]
        self.ledger.update(BASE, samples, end)
        result = self.total(end)
        self.assertAlmostEqual(result["net_cost"], 3.0)
        self.assertEqual(result["solar_kwh"], 12)
        self.assertEqual(result["usage_kwh"], 18)
        self.ledger = Ledger(self.ledger.path, "Europe/Dublin", "EUR")
        self.ledger.initialize(end)
        self.ledger.update(BASE, samples, end)
        self.assertEqual(self.total(end), result)
        report = self.ledger.report(date(2026, 1, 1), date(2026, 1, 1), "day")
        self.assertEqual(report["totals"]["net_cost"], 3.0)
        self.assertFalse(report["partial_history"])

    def test_time_band_midnight_and_weekend(self):
        config = {
            **BASE,
            "bands": [
                {
                    "name": "Night",
                    "start": "23:00",
                    "end": "07:00",
                    "days": ["0", "1", "2", "3", "4"],
                    "import_rate": 0.1,
                    "export_rate": 0.2,
                }
            ],
        }
        validate_tariff(config)
        self.ledger.update(config, [], self.start + 1)
        end = self.start + 86400
        self.ledger.update(config, [sample("import", 124, end)], end)
        # 8 off-peak hours, 16 standard hours, at 1 kWh/hour.
        self.assertAlmostEqual(self.total(end)["import_cost"], 5.6 + 0.2 / 3600)
        self.assertEqual(rates_at(config, datetime(2026, 1, 3, 6)), (0.1, 0.2))
        self.assertEqual(rates_at(config, datetime(2026, 1, 4, 6)), (0.3, 0.15))

    def test_fractional_kwh_preserves_watt_hours_and_costs(self):
        end = self.start + 60
        deltas = {"solar": 0.123, "usage": 0.001, "import": 0.025, "export": 0.007}
        self.ledger.update(
            BASE, [sample(source, 100 + delta, end) for source, delta in deltas.items()], end
        )
        result = self.total(end)
        for source, delta in deltas.items():
            self.assertAlmostEqual(result[f"{source}_kwh"], delta, places=9)
        self.assertAlmostEqual(result["import_cost"], 0.025 * 0.30, places=9)
        self.assertAlmostEqual(result["export_credit"], 0.007 * 0.15, places=9)

    def test_rate_change_preserves_unreported_intervals(self):
        half = self.start + 43200
        self.ledger.update({**BASE, "import_rate": 0.5, "standing_charge": 1.2}, [], half)
        end = self.start + 86400
        self.ledger.update(
            {**BASE, "import_rate": 0.5, "standing_charge": 1.2}, [sample("import", 124, end)], end
        )
        self.assertAlmostEqual(self.total(end)["import_cost"], 9.6)
        self.assertAlmostEqual(self.total(end)["standing_charge"], 0.9)

    def test_reset_correction_out_of_order_and_replacement(self):
        at = self.start + 3600
        self.ledger.update(
            BASE,
            [sample("import", 102, at), sample("import", 0, at + 1), sample("import", 105, at + 2)],
            at + 2,
        )
        self.assertAlmostEqual(self.total(at + 2)["import_kwh"], 107)
        self.ledger.update(BASE, [sample("import", 104, at + 3, resetting=False)], at + 3)
        self.ledger.update(BASE, [sample("import", 900, at)], at + 4)
        self.ledger.update(
            BASE, [sample("import", 9000, at + 5, entity="sensor.replacement")], at + 5
        )
        self.ledger.update(
            BASE, [sample("import", 9002, at + 6, entity="sensor.replacement")], at + 6
        )
        self.assertAlmostEqual(self.total(at + 6)["import_kwh"], 109)

    def test_explicit_reset_even_when_new_value_exceeds_old(self):
        at = self.start + 3600
        self.ledger.update(BASE, [sample("import", 120, at, last_reset=self.start + 1800)], at)
        self.assertEqual(self.total(at)["import_kwh"], 120)

    def test_negative_prices_and_validation(self):
        config = {**BASE, "import_rate": -0.1, "export_rate": -0.05}
        self.ledger.update(
            config,
            [sample("import", 100, self.start + 1), sample("export", 100, self.start + 1)],
            self.start + 1,
        )
        self.ledger.update(
            config,
            [sample("import", 101, self.start + 2), sample("export", 102, self.start + 2)],
            self.start + 2,
        )
        self.assertAlmostEqual(self.total(self.start + 2)["import_cost"], -0.1)
        self.assertAlmostEqual(self.total(self.start + 2)["export_credit"], -0.1)
        for number in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValueError):
                validate_tariff({**BASE, "standing_charge": number})
        band = {
            "name": "Night",
            "start": "23:00",
            "end": "07:00",
            "days": ["6"],
            "import_rate": 0.1,
            "export_rate": 0.1,
        }
        for change in ({"start": "25:00"}, {"end": "23:00"}, {"days": []}, {"start": "22:00:01"}):
            with self.assertRaises(ValueError):
                validate_tariff({**BASE, "bands": [{**band, **change}]})
        with self.assertRaises(ValueError):
            validate_tariff(
                {
                    **BASE,
                    "bands": [
                        band,
                        {**band, "name": "Monday", "days": ["0"], "start": "06:00", "end": "09:00"},
                    ],
                }
            )

    def test_atomic_rollback(self):
        with self.assertRaises(ValueError):
            self.ledger.update(
                BASE, [sample("import", float("nan"), self.start + 3600)], self.start + 3600
            )
        self.assertEqual(self.total(self.start)["standing_charge"], 0)

    def test_dst_standing_and_missing_tariff_boundary(self):
        zone = ZoneInfo("Europe/Dublin")
        for day, hours in ((date(2026, 3, 29), 23), (date(2026, 10, 25), 25)):
            start = datetime(day.year, day.month, day.day, tzinfo=zone).timestamp()
            end = start + hours * 3600
            ledger = Ledger(Path(self.temp.name) / f"{day}.db", zone.key, "EUR")
            config = {
                **BASE,
                "import_rate": 0.2,
                "bands": [
                    {
                        "name": "Cheap",
                        "start": "01:30",
                        "end": "02:30",
                        "days": ["6"],
                        "import_rate": -0.2,
                        "export_rate": 0.1,
                    }
                ],
            }
            ledger.initialize(start)
            ledger.update(config, [sample("import", 0, start)], start)
            ledger.update(config, [sample("import", hours, end)], end)
            result = ledger.snapshot(end, 12, 5)["periods"]["all_time"]
            self.assertAlmostEqual(result["standing_charge"], 0.6)
            # Spring: 30 cheap minutes; autumn: 90 cheap minutes across folds.
            self.assertAlmostEqual(result["import_cost"], 4.4)
            self.assertEqual(result["import_kwh"], hours)

    def test_calendar_ranges_and_long_history(self):
        starts = period_starts(date(2028, 2, 29), 12, 5)
        self.assertEqual(starts["last_months"], date(2027, 3, 1))
        self.assertEqual(starts["last_years"], date(2024, 1, 1))
        self.assertEqual(starts["week"], date(2028, 2, 28))
        end = timestamp("2036-01-01T00:00:00")
        self.ledger.update(BASE, [sample("solar", 100 + 3652, end)], end)
        with sqlite3.connect(self.ledger.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM daily").fetchone()[0], 3652)
        report = self.ledger.report(date(2026, 1, 1), date(2035, 12, 31), "year")
        self.assertEqual(len(report["periods"]), 10)
        self.assertAlmostEqual(report["totals"]["standing_charge"], 3652 * 0.6)
        self.assertAlmostEqual(report["totals"]["solar_kwh"], 3652)
        self.assertTrue(
            self.ledger.snapshot(end, 1200, 100)["periods"]["last_years"]["partial_history"]
        )

    def test_vat_discount_and_calendar_month_levy(self):
        config = {
            **BASE,
            "import_rate": 0.3969,
            "export_rate": 0.185,
            "standing_charge": 0.84,
            "monthly_charge": 1.46,
            "vat_percent": 9,
            "discount_percent": 20,
        }
        ledger = Ledger(Path(self.temp.name) / "tax.db", "Europe/Dublin", "EUR")
        ledger.initialize(self.start)
        ledger.update(config, [], self.start)
        end = timestamp("2026-02-01T00:00:00")
        ledger.update(config, [], end)
        result = ledger.snapshot(end, 12, 5)["periods"]["all_time"]
        self.assertAlmostEqual(result["standing_charge"], 31 * 0.84 * 1.09)
        self.assertAlmostEqual(result["fixed_charge"], 1.46 * 1.09)
        self.assertEqual(rates_at(config, datetime(2026, 1, 1)), (0.3969 * 0.8 * 1.09, 0.185))
        for field in ("vat_percent", "discount_percent"):
            with self.assertRaises(ValueError):
                validate_tariff({**config, field: 101})


if __name__ == "__main__":
    unittest.main()
