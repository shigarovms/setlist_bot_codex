"""Build a compact offline name index from the geonamescache 3.0.2 wheel (GeoNames)."""

import argparse
import json
import re
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    with zipfile.ZipFile(args.wheel) as archive:
        cities = json.loads(archive.read("geonamescache/data/cities15000.json"))
    names = {}
    # RU: Крупные города первыми разрешают совпадения названий; координаты не нужны.
    # EN: This is a text label dictionary, not geocoding or a country inference service.
    for city in sorted(cities.values(), key=lambda item: item["population"]):
        for name in [city["name"], *city["alternatenames"]]:
            if name == city["name"] or re.fullmatch(r"[А-Яа-яЁё -]{3,80}", name):
                names[" ".join(name.lower().replace("ё", "е").split())] = name
    target = Path(__file__).resolve().parents[1] / "src/setlist_bot/domain/data/cities.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(names, ensure_ascii=False, sort_keys=True, indent=0) + "\n")
    print(f"{len(names)} offline city labels written")


if __name__ == "__main__":
    main()
