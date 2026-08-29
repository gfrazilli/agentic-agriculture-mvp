"""Windowed Cloud Optimized GeoTIFF reads for the analysis worker."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import numpy as np

from geospatial.provenance import REQUIRED_BAND_ALIASES
from geospatial.tools import resolve_required_band_assets

_ALLOWED_ASSET_HOST_SUFFIXES = (
    ".amazonaws.com",
    ".earth-search.aws.element84.com",
)


class UnsafeAssetURLError(ValueError):
    """An asset URL is not a public HTTPS Sentinel object."""


class MissingRasterDependency(ImportError):
    """Rasterio is needed only by the real COG acquisition path."""


@dataclass(frozen=True, slots=True)
class RasterWindow:
    data: np.ndarray
    valid_mask: np.ndarray
    transform: tuple[float, float, float, float, float, float]
    crs: str
    nodata: float | int | None


@dataclass(frozen=True, slots=True)
class MultibandWindow:
    bands: Mapping[str, RasterWindow]

    @property
    def shape(self) -> tuple[int, int]:
        first = next(iter(self.bands.values()))
        return first.data.shape


def validate_asset_url(url: str) -> str:
    """Reject non-HTTPS and non-AWS hosts before Rasterio performs network I/O."""

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname:
        raise UnsafeAssetURLError("Sentinel assets must use an absolute HTTPS URL.")
    approved_host = any(
        hostname == suffix[1:] or hostname.endswith(suffix)
        for suffix in _ALLOWED_ASSET_HOST_SUFFIXES
    )
    if not approved_host:
        raise UnsafeAssetURLError("The asset host is outside the approved Earth Search/AWS path.")
    if parsed.username or parsed.password or parsed.fragment:
        raise UnsafeAssetURLError("Asset URLs cannot contain credentials or fragments.")
    return url


def _rasterio() -> Any:
    try:
        rasterio = importlib.import_module("rasterio")
        # Rasterio does not guarantee that its submodules are attributes after
        # importing only the top-level package.
        rasterio.warp = importlib.import_module("rasterio.warp")
        rasterio.windows = importlib.import_module("rasterio.windows")
        rasterio.enums = importlib.import_module("rasterio.enums")
        return rasterio
    except ImportError as exc:  # pragma: no cover - exercised only in minimal installs
        raise MissingRasterDependency(
            "rasterio is required to read Sentinel Cloud Optimized GeoTIFF assets"
        ) from exc


class COGWindowReader:
    """Read a bounded, size-limited window from public Sentinel COGs."""

    def read(
        self,
        url: str,
        *,
        bbox_wgs84: tuple[float, float, float, float],
        max_dimension: int = 768,
        categorical: bool = False,
    ) -> RasterWindow:
        if not 32 <= max_dimension <= 2048:
            raise ValueError("max_dimension must be between 32 and 2048")
        rasterio = _rasterio()
        url = validate_asset_url(url)
        with rasterio.Env(AWS_NO_SIGN_REQUEST="YES", GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
            with rasterio.open(url) as dataset:
                projected_bounds = rasterio.warp.transform_bounds(
                    "EPSG:4326", dataset.crs, *bbox_wgs84, densify_pts=21
                )
                window = rasterio.windows.from_bounds(
                    *projected_bounds, transform=dataset.transform
                )
                window = window.round_offsets().round_lengths()
                source_height = max(1, int(window.height))
                source_width = max(1, int(window.width))
                scale = min(1.0, max_dimension / max(source_height, source_width))
                out_height = max(1, round(source_height * scale))
                out_width = max(1, round(source_width * scale))
                resampling = (
                    rasterio.enums.Resampling.nearest
                    if categorical
                    else rasterio.enums.Resampling.bilinear
                )
                masked = dataset.read(
                    1,
                    window=window,
                    out_shape=(out_height, out_width),
                    masked=True,
                    boundless=True,
                    resampling=resampling,
                )
                valid_mask = ~np.ma.getmaskarray(masked)
                if not np.any(valid_mask):
                    raise ValueError("The requested bounds do not contain valid scene pixels.")
                transform = dataset.window_transform(window) * dataset.transform.scale(
                    source_width / out_width, source_height / out_height
                )
                return RasterWindow(
                    data=np.asarray(masked.astype(np.float32).filled(np.nan)),
                    valid_mask=valid_mask,
                    transform=tuple(transform)[:6],
                    crs=str(dataset.crs),
                    nodata=dataset.nodata,
                )

    def read_required_bands(
        self,
        assets: Mapping[str, str],
        *,
        bbox_wgs84: tuple[float, float, float, float],
        max_dimension: int = 768,
    ) -> MultibandWindow:
        resolved = resolve_required_band_assets(assets)
        missing = set(REQUIRED_BAND_ALIASES) - resolved.keys()
        if missing:
            raise ValueError(f"Scene is missing required assets: {', '.join(sorted(missing))}")
        windows = {
            band: self.read(
                url,
                bbox_wgs84=bbox_wgs84,
                max_dimension=max_dimension,
                categorical=band == "SCL",
            )
            for band, url in resolved.items()
        }
        shapes = {window.data.shape for window in windows.values()}
        if len(shapes) != 1:
            raise ValueError("Resampled Sentinel bands must share one output shape.")
        return MultibandWindow(bands=windows)
