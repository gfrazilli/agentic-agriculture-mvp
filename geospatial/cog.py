"""Windowed Cloud Optimized GeoTIFF reads for the analysis worker."""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import numpy as np

from geospatial.provenance import REQUIRED_BAND_ALIASES
from geospatial.tools import resolve_required_band_assets

_SENTINEL_COG_HOST = re.compile(
    r"sentinel-cogs\.s3(?:[.-][a-z0-9-]+)?\.amazonaws\.com",
    flags=re.ASCII,
)

_GDAL_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_CONNECTTIMEOUT": "10",
    "GDAL_HTTP_TIMEOUT": "30",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
}


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
    if _SENTINEL_COG_HOST.fullmatch(hostname) is None:
        raise UnsafeAssetURLError("The asset host is outside the approved Earth Search/AWS path.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise UnsafeAssetURLError("The asset URL contains an invalid port.") from exc
    if port not in {None, 443}:
        raise UnsafeAssetURLError("Sentinel assets may use only the standard HTTPS port.")
    if parsed.username or parsed.password or parsed.fragment or not parsed.path.startswith("/"):
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
        out_shape: tuple[int, int] | None = None,
        categorical: bool = False,
        scale: float | None = None,
        offset: float | None = None,
    ) -> RasterWindow:
        if not 32 <= max_dimension <= 2048:
            raise ValueError("max_dimension must be between 32 and 2048")
        rasterio = _rasterio()
        url = validate_asset_url(url)
        with rasterio.Env(**_GDAL_ENV):
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
                if out_shape is None:
                    resize_scale = min(1.0, max_dimension / max(source_height, source_width))
                    out_height = max(1, round(source_height * resize_scale))
                    out_width = max(1, round(source_width * resize_scale))
                else:
                    out_height, out_width = out_shape
                    if not 1 <= out_height <= 2048 or not 1 <= out_width <= 2048:
                        raise ValueError("out_shape dimensions must be between 1 and 2048")
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
                data = np.asarray(masked.astype(np.float32).filled(np.nan))
                if not categorical:
                    raster_scale = (
                        float(scale)
                        if scale is not None
                        else (float(dataset.scales[0]) if dataset.scales else 1.0)
                    )
                    raster_offset = (
                        float(offset)
                        if offset is not None
                        else (float(dataset.offsets[0]) if dataset.offsets else 0.0)
                    )
                    data = data * raster_scale + raster_offset
                transform = dataset.window_transform(window) * dataset.transform.scale(
                    source_width / out_width, source_height / out_height
                )
                return RasterWindow(
                    data=data,
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
        out_shape: tuple[int, int] | None = None,
        reference_grid: RasterWindow | None = None,
        calibration: Mapping[str, tuple[float, float]] | None = None,
    ) -> MultibandWindow:
        resolved = resolve_required_band_assets(assets)
        missing = set(REQUIRED_BAND_ALIASES) - resolved.keys()
        if missing:
            raise ValueError(f"Scene is missing required assets: {', '.join(sorted(missing))}")
        band_calibration = {
            band: _resolve_calibration(calibration or {}, band) for band in REQUIRED_BAND_ALIASES
        }
        if reference_grid is None:
            red_scale, red_offset = band_calibration["B04"]
            reference = self.read(
                resolved["B04"],
                bbox_wgs84=bbox_wgs84,
                max_dimension=max_dimension,
                out_shape=out_shape,
                scale=red_scale,
                offset=red_offset,
            )
            windows = {"B04": reference}
        else:
            reference = reference_grid
            red_scale, red_offset = band_calibration["B04"]
            windows = {
                "B04": self.read_aligned(
                    resolved["B04"],
                    reference=reference,
                    scale=red_scale,
                    offset=red_offset,
                )
            }
        for band, url in resolved.items():
            if band == "B04":
                continue
            scale, offset = band_calibration[band]
            windows[band] = self.read_aligned(
                url,
                reference=reference,
                categorical=band == "SCL",
                scale=scale,
                offset=offset,
            )
        return MultibandWindow(bands=windows)

    def read_aligned(
        self,
        url: str,
        *,
        reference: RasterWindow,
        categorical: bool = False,
        scale: float | None = None,
        offset: float | None = None,
    ) -> RasterWindow:
        """Reproject one COG onto the exact grid of a previously-read band."""

        rasterio = _rasterio()
        url = validate_asset_url(url)
        destination = np.full(reference.data.shape, np.nan, dtype=np.float32)
        destination_transform = rasterio.Affine(*reference.transform)
        resampling = (
            rasterio.enums.Resampling.nearest if categorical else rasterio.enums.Resampling.bilinear
        )
        with rasterio.Env(**_GDAL_ENV):
            with rasterio.open(url) as dataset:
                rasterio.warp.reproject(
                    source=rasterio.band(dataset, 1),
                    destination=destination,
                    src_transform=dataset.transform,
                    src_crs=dataset.crs,
                    src_nodata=dataset.nodata,
                    dst_transform=destination_transform,
                    dst_crs=reference.crs,
                    dst_nodata=np.nan,
                    resampling=resampling,
                )
                if not categorical:
                    raster_scale = (
                        float(scale)
                        if scale is not None
                        else (float(dataset.scales[0]) if dataset.scales else 1.0)
                    )
                    raster_offset = (
                        float(offset)
                        if offset is not None
                        else (float(dataset.offsets[0]) if dataset.offsets else 0.0)
                    )
                    destination = destination * raster_scale + raster_offset
        valid_mask = np.isfinite(destination)
        if not np.any(valid_mask):
            raise ValueError("The requested bounds do not contain valid scene pixels.")
        return RasterWindow(
            data=destination,
            valid_mask=valid_mask,
            transform=reference.transform,
            crs=reference.crs,
            nodata=np.nan,
        )


def _resolve_calibration(
    calibration: Mapping[str, tuple[float, float]], band: str
) -> tuple[float | None, float | None]:
    for alias in REQUIRED_BAND_ALIASES[band]:
        value = calibration.get(alias)
        if value is None:
            continue
        if len(value) != 2:
            raise ValueError(f"Calibration for {band} must contain scale and offset.")
        scale, offset = float(value[0]), float(value[1])
        if not np.isfinite(scale) or scale <= 0 or not np.isfinite(offset):
            raise ValueError(f"Calibration for {band} is invalid.")
        return scale, offset
    return None, None
