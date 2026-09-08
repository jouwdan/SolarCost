"""SolarCost constants."""

DOMAIN = "solarcost"
SOURCES = ("solar", "usage", "import", "export")
ENERGY = tuple(f"{source}_kwh" for source in SOURCES)
MONEY = ("import_cost", "export_credit", "standing_charge", "fixed_charge", "net_cost")
METRICS = ENERGY + MONEY
PERIODS = ("today", "week", "month", "last_months", "year", "last_years", "all_time")
DEFAULTS = {
    "import_rate": 0.0,
    "export_rate": 0.0,
    "standing_charge": 0.0,
    "monthly_charge": 0.0,
    "vat_percent": 0.0,
    "discount_percent": 0.0,
    "last_months": 12,
    "last_years": 5,
    "bands": [],
}
