"""Check the actual installable archive before uploading or publishing it."""

import json
import re
import sys
from pathlib import Path
from zipfile import ZipFile

manifest = json.loads(Path("custom_components/solarcost/manifest.json").read_text())
assert re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", manifest["version"]), (
    "Releases require a stable major.minor.patch version in manifest.json"
)

with ZipFile(sys.argv[1]) as archive:
    assert archive.testzip() is None, "Corrupt release archive"
    assert json.loads(archive.read("manifest.json")) == manifest, "Archive manifest mismatch"
    required = {
        "LICENSE",
        "__init__.py",
        "config_flow.py",
        "coordinator.py",
        "const.py",
        "ledger.py",
        "sensor.py",
        "strings.json",
        "translations/en.json",
        "services.yaml",
        "brand/icon.png",
        "solarcost-card.js",
        "solarcost-tou-card.js",
        "card.yaml",
        "power_meters.yaml",
    }
    assert required <= set(archive.namelist()), "Release is missing integration assets"
    for name in archive.namelist():
        assert not name.startswith(("/", "custom_components/", "tests/")), name
        assert ".." not in Path(name).parts and "__pycache__" not in Path(name).parts, name
        if not name.endswith("/"):
            source = (
                Path("LICENSE") if name == "LICENSE" else Path("custom_components/solarcost", name)
            )
            assert archive.read(name) == source.read_bytes(), name

print(f"Release archive passed: SolarCost {manifest['version']}")
