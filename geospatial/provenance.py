"""Canonical provenance for the public Sentinel-2 data path used by the MVP."""

DATA_PRODUCER = "EU/ESA/Copernicus"
MISSION = "Sentinel-2"
PRODUCT_LEVEL = "L2A"
CATALOG_PROVIDER = "Element 84 Earth Search"
OBJECT_PROVIDER = "AWS Open Data"
EARTH_SEARCH_URL = "https://earth-search.aws.element84.com/v1"
EARTH_SEARCH_COLLECTION = "sentinel-2-l2a"

REQUIRED_BAND_ALIASES: dict[str, tuple[str, ...]] = {
    "B04": ("red", "B04", "b04"),
    "B05": ("rededge1", "rededge", "B05", "b05"),
    "B08": ("nir", "nir08", "B08", "b08"),
    "B11": ("swir16", "swir", "B11", "b11"),
    "SCL": ("scl", "SCL"),
}


def provenance_payload() -> dict[str, str]:
    """Return truthful producer/access metadata suitable for persisted results."""

    return {
        "data_producer": DATA_PRODUCER,
        "mission": MISSION,
        "product_level": PRODUCT_LEVEL,
        "catalog_provider": CATALOG_PROVIDER,
        "object_provider": OBJECT_PROVIDER,
        "catalog_url": EARTH_SEARCH_URL,
        "collection": EARTH_SEARCH_COLLECTION,
    }
