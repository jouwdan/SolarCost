"""Block billing, using the issue's illustrative rates rather than a live supplier tariff."""

import json
import random
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# The existing test module initializes the pure accounting package.
# isort: off
from test_ledger import BASE, ROOT, Ledger, sample, timestamp
from solarcost_accounting.ledger import block_charges, validate_tariff
# isort: on

BLOCK = {
    **BASE,
    "standing_charge": 0,
    "export_rate": 0,
    **json.loads((ROOT / "examples/block_tariff.json").read_text()),
}


def expected(kwh):
    """Independent arithmetic for the example's three category scales."""
    if kwh <= 60:
        return 5 * min(kwh, 30) + 9 * max(kwh - 30, 0), 80 if kwh <= 30 else 210
    if kwh <= 180:
        cost = 840 + 20 * min(kwh - 60, 30)
        cost += 28 * min(max(kwh - 90, 0), 30) + 44 * max(kwh - 120, 0)
        return cost, 400 if kwh <= 90 else 1000 if kwh <= 120 else 1500
    return 5850 + 100 * (kwh - 180), 2500


class BlockAccountingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.start = timestamp("2026-01-01T00:00:00")
        self.ledger = Ledger(Path(self.temp.name) / "bill.db", "Europe/Dublin", "LKR")
        self.ledger.initialize(self.start)
        self.ledger.update(BLOCK, [sample("import", 100, self.start)], self.start)

    def month(self, now):
        return self.ledger.snapshot(now, 12, 5)["periods"]["month"]

    def test_every_exact_boundary_and_fractional_crossing(self):
        for quantity in (
            0,
            0.001,
            30,
            30.001,
            60,
            60.001,
            90,
            90.001,
            120,
            120.001,
            180,
            180.001,
            181,
            1000,
        ):
            with self.subTest(quantity=quantity):
                result = block_charges(BLOCK, quantity)
                cost, fixed = expected(quantity)
                self.assertAlmostEqual(result["import_cost"], cost)
                self.assertEqual(result["fixed_charge"], fixed)
                self.assertAlmostEqual(
                    sum(row["import_kwh"] for row in result["rows"].values()), quantity
                )
        self.assertEqual(block_charges(BLOCK, sum([0.1] * 600))["category"], "Low consumption")

    def test_threshold_reprices_previous_days_and_accrued_fixed_fee(self):
        first, second = self.start + 86400, self.start + 2 * 86400
        self.ledger.update(BLOCK, [sample("import", 160, first)], first)
        self.assertAlmostEqual(self.month(first)["import_cost"], 420)
        self.assertAlmostEqual(self.month(first)["fixed_charge"], 210 / 31)
        self.ledger.update(BLOCK, [sample("import", 160.001, second)], second)
        result = self.month(second)
        self.assertAlmostEqual(result["import_cost"], 840.02)
        self.assertAlmostEqual(result["fixed_charge"], 400 * 2 / 31)
        report = self.ledger.report(date(2026, 1, 1), date(2026, 1, 2), "day")
        self.assertAlmostEqual(report["periods"][0]["import_cost"], 840)
        self.assertAlmostEqual(report["periods"][1]["import_cost"], 0.02)
        self.assertAlmostEqual(
            sum(row["net_cost"] for row in report["periods"]), result["net_cost"], places=6
        )
        self.assertTrue(all(not key.startswith("block:Low") for key in result["time_of_use"]))

    def test_seeded_meter_updates_match_independent_monthly_bill(self):
        randomizer = random.Random(6)
        value, quantity, now = 100, 0, self.start
        for index in range(160):
            now += randomizer.randint(60, 12000)
            delta = randomizer.randint(1, 3500) / 1000
            value += delta
            quantity += delta
            self.ledger.update(BLOCK, [sample("import", value, now)], now)
            result = self.month(now)
            cost, fixed = expected(quantity)
            with self.subTest(update=index):
                self.assertAlmostEqual(result["import_cost"], cost, places=6)
                self.assertAlmostEqual(
                    result["fixed_charge"], fixed * (now - self.start) / (31 * 86400), places=6
                )
                for metric in ("import_kwh", "import_cost"):
                    self.assertAlmostEqual(
                        sum(row[metric] for row in result["time_of_use"].values()),
                        result[metric],
                        places=6,
                    )

    def test_tariff_changes_keep_original_prices_and_monthly_block_position(self):
        first, second = self.start + 86400, self.start + 2 * 86400
        self.ledger.update(BLOCK, [sample("import", 160, first)], first)
        changed = deepcopy(BLOCK)
        for category in changed["block_categories"]:
            for block in category["blocks"]:
                block["rate"] *= 2
                block["fixed_charge"] *= 2
        self.ledger.update(changed, [], first)
        self.ledger.update(changed, [sample("import", 161, second)], second)
        result = self.month(second)
        self.assertAlmostEqual(result["import_cost"], 840 + 40)
        self.assertAlmostEqual(result["fixed_charge"], (400 + 800) / 31)
        self.ledger.initialize(second)
        self.ledger.update(changed, [sample("import", 161, second)], second)
        self.assertEqual(self.month(second), result)

    def test_month_reset_multi_month_reports_and_restart(self):
        boundary = timestamp("2026-02-01T00:00:00")
        self.ledger.update(BLOCK, [sample("import", 281, boundary)], boundary)
        previous = self.ledger.snapshot(boundary, 12, 5)["periods"]["last_month"]
        self.assertAlmostEqual(previous["import_cost"], 5950)
        self.assertAlmostEqual(previous["fixed_charge"], 2500)
        end = timestamp("2026-03-01T00:00:00")
        self.ledger.update(BLOCK, [sample("import", 311, end)], end)
        report = self.ledger.report(date(2026, 1, 1), date(2026, 2, 28), "month")
        self.assertEqual([row["import_cost"] for row in report["periods"]], [5950, 150])
        self.assertAlmostEqual(report["totals"]["fixed_charge"], 2580)
        for grouping in ("day", "week", "month", "year"):
            grouped = self.ledger.report(date(2026, 1, 1), date(2026, 2, 28), grouping)
            self.assertAlmostEqual(
                sum(row["net_cost"] for row in grouped["periods"]),
                report["totals"]["net_cost"],
                delta=len(grouped["periods"]) * 0.000001,
            )
        self.ledger = Ledger(self.ledger.path, "Europe/Dublin", "LKR")
        self.ledger.initialize(end)
        self.ledger.update(BLOCK, [sample("import", 311, end)], end)
        self.assertEqual(self.ledger.report(date(2026, 1, 1), date(2026, 2, 28), "month"), report)

    def test_unreported_interval_spans_tariff_change_and_category_crossing(self):
        first, end = self.start + 86400, self.start + 2 * 86400
        self.ledger.update(BLOCK, [sample("import", 160, first)], first)
        changed = deepcopy(BLOCK)
        for category in changed["block_categories"]:
            for block in category["blocks"]:
                block["rate"] *= 2
                block["fixed_charge"] *= 2
        self.ledger.update(changed, [], first + 43200)
        self.ledger.update(changed, [sample("import", 161, end)], end)
        result = self.month(end)
        self.assertAlmostEqual(result["import_cost"], 840 + 10 + 20)
        self.assertAlmostEqual(result["fixed_charge"], (400 * 1.5 + 800 * 0.5) / 31)

    def test_zero_import_still_accrues_lowest_tier_fee_and_overflow_rolls_back(self):
        end = self.start + 86400
        self.ledger.update(BLOCK, [], end)
        self.assertEqual(self.month(end)["import_cost"], 0)
        self.assertAlmostEqual(self.month(end)["fixed_charge"], 80 / 31)
        bad = {**BLOCK, "block_categories": [{"name": "Overflow", "blocks": [{"rate": 1e308}]}]}
        before = self.month(end)
        with self.assertRaises(ValueError):
            self.ledger.update(bad, [sample("import", 1e308, end + 1)], end + 1)
        self.assertEqual(self.month(end), before)

    def test_dst_leap_month_and_non_utc_month_boundaries(self):
        for zone_name, left, right, days in (
            ("Europe/Dublin", "2026-03-01", "2026-04-01", 31),
            ("Europe/Dublin", "2026-10-01", "2026-11-01", 31),
            ("Europe/Dublin", "2028-02-01", "2028-03-01", 29),
            ("Asia/Colombo", "2026-01-01", "2026-02-01", 31),
        ):
            zone = ZoneInfo(zone_name)
            start, end = (
                datetime.fromisoformat(day).replace(tzinfo=zone).timestamp()
                for day in (left, right)
            )
            ledger = Ledger(
                Path(self.temp.name) / f"{left}-{zone_name.replace('/', '-')}.db", zone_name, "LKR"
            )
            ledger.initialize(start)
            ledger.update(BLOCK, [sample("import", 0, start)], start)
            ledger.update(BLOCK, [sample("import", 30, end)], end)
            result = ledger.snapshot(end, 12, 5)["periods"]["last_month"]
            self.assertAlmostEqual(result["fixed_charge"], 80)
            self.assertAlmostEqual(result["import_cost"], 150)
            report = ledger.report(date.fromisoformat(left), date.fromisoformat(right), "day")
            self.assertEqual(len(report["periods"]), days)

    def test_tax_discount_export_and_additional_fees(self):
        config = {
            **BLOCK,
            "vat_percent": 15,
            "discount_percent": 10,
            "export_rate": 2,
            "standing_charge": 1,
            "monthly_charge": 31,
        }
        self.ledger.update(config, [], self.start + 1)
        # Establish the new prices before the measured interval.
        self.ledger.update(
            config,
            [sample("import", 100, self.start + 1), sample("export", 0, self.start + 1)],
            self.start + 1,
        )
        end = self.start + 86401
        self.ledger.update(config, [sample("import", 110, end), sample("export", 3, end)], end)
        result = self.month(end)
        self.assertAlmostEqual(result["import_cost"], 50 * 0.9 * 1.15)
        self.assertAlmostEqual(result["export_credit"], 6)
        self.assertAlmostEqual(result["fixed_charge"], (80 / 31 + 1) * 1.15 + 80 / (31 * 86400))
        self.assertAlmostEqual(result["standing_charge"], 1.15)

    def test_meter_reset_replacement_and_invalid_sample_rollback(self):
        first = self.start + 86400
        self.ledger.update(BLOCK, [sample("import", 130, first)], first)
        self.ledger.update(
            BLOCK, [sample("import", 0, first + 1), sample("import", 31, first + 2)], first + 2
        )
        self.assertAlmostEqual(self.month(first + 2)["import_cost"], 860)
        self.ledger.update(
            BLOCK, [sample("import", 900, first + 3, entity="sensor.new")], first + 3
        )
        self.ledger.update(
            BLOCK, [sample("import", 901, first + 4, entity="sensor.new")], first + 4
        )
        before = self.month(first + 4)
        self.assertEqual(before["import_kwh"], 62)
        with self.assertRaises(ValueError):
            self.ledger.update(
                BLOCK,
                [
                    sample("import", 905, first + 5, entity="sensor.new"),
                    sample("solar", float("nan"), first + 5),
                ],
                first + 5,
            )
        self.assertEqual(self.month(first + 4), before)

    def test_upgrade_and_switching_models_preserve_flat_history(self):
        ledger = Ledger(Path(self.temp.name) / "upgrade.db", "Europe/Dublin", "LKR")
        flat = {**BASE, "standing_charge": 0, "export_rate": 0}
        ledger.initialize(self.start)
        ledger.update(flat, [sample("import", 0, self.start)], self.start)
        first, second = self.start + 86400, self.start + 2 * 86400
        ledger.update(flat, [sample("import", 60, first)], first)
        before = ledger.snapshot(first, 12, 5)
        with sqlite3.connect(ledger.path) as db:
            db.execute("DROP TABLE daily_blocks")
            db.execute("PRAGMA user_version=2")
        ledger.initialize(first)
        self.assertEqual(ledger.snapshot(first, 12, 5), before)
        ledger.update(BLOCK, [], first)
        ledger.update(BLOCK, [sample("import", 61, second)], second)
        result = ledger.snapshot(second, 12, 5)["periods"]["month"]
        self.assertAlmostEqual(result["import_cost"], 18 + 20)
        self.assertAlmostEqual(result["time_of_use"]["base"]["import_cost"], 18)
        ledger.update(flat, [], second)
        end = second + 86400
        ledger.update(flat, [sample("import", 62, end)], end)
        self.assertAlmostEqual(
            ledger.snapshot(end, 12, 5)["periods"]["month"]["import_cost"], 18.3 + 20
        )

    def test_invalid_structures_and_numbers_are_rejected(self):
        invalid = [[], {}, None]
        for categories in invalid:
            with self.assertRaises((ValueError, TypeError)):
                validate_tariff({**BLOCK, "block_categories": categories})
        for path, value in (
            (("up_to",), 0),
            (("up_to",), True),
            (("name",), ""),
            (("blocks",), []),
            (("blocks", 0, "up_to"), 0),
            (("blocks", 0, "rate"), float("nan")),
            (("blocks", 0, "fixed_charge"), -1),
            (("blocks", 1, "up_to"), 59),
        ):
            config = deepcopy(BLOCK)
            node = config["block_categories"][0]
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path, value=value), self.assertRaises((ValueError, TypeError)):
                validate_tariff(config)
        for mutate in ("duplicate", "unlimited_first", "bounded_last", "too_many"):
            config = deepcopy(BLOCK)
            categories = config["block_categories"]
            if mutate == "duplicate":
                categories[1]["name"] = categories[0]["name"]
            elif mutate == "unlimited_first":
                categories[0].pop("up_to")
            elif mutate == "bounded_last":
                categories[-1]["up_to"] = 200
            else:
                categories[0]["blocks"] *= 49
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                validate_tariff(config)

    def test_single_progressive_category_partial_setup_and_negative_rates(self):
        config = {
            **BLOCK,
            "block_categories": [
                {
                    "name": "Progressive",
                    "blocks": [
                        {"up_to": 30, "rate": -1, "fixed_charge": 31},
                        {"rate": 2, "fixed_charge": 62},
                    ],
                }
            ],
        }
        validate_tariff(config)
        self.assertEqual(block_charges(config, 40)["import_cost"], -10)
        start = timestamp("2026-01-15T12:00:00")
        ledger = Ledger(Path(self.temp.name) / "partial.db", "Europe/Dublin", "LKR")
        ledger.initialize(start)
        ledger.update(config, [sample("import", 0, start)], start)
        end = start + 86400
        ledger.update(config, [sample("import", 10, end)], end)
        result = ledger.snapshot(end, 12, 5)["periods"]["month"]
        self.assertEqual(result["import_cost"], -10)
        self.assertAlmostEqual(result["fixed_charge"], 1)
        self.assertTrue(result["partial_history"])
