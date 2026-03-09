# app/utils/geo.py
from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

import geopandas as gpd
import requests
from shapely.geometry import box

DEFAULT_NAME_FIELD = "name"

# ----------------------------
# Defaults
# ----------------------------
LINZ_WFS_BASE_DEFAULT = "https://data.linz.govt.nz/services/wfs"
DATAFINDER_WFS_BASE_DEFAULT = "https://datafinder.stats.govt.nz/services/wfs"

# Some WFS services require namespace in typename, some don’t.
# We’ll discover from GetCapabilities where possible.


# ----------------------------
# Helpers
# ----------------------------
def _env(*names: str) -> Optional[str]:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def _wfs_base_with_key(base: str, key: Optional[str]) -> str:
    """
    Normalize WFS base URL + key injection.

    Supports:
      - ".../services;key=XYZ/wfs" (preferred for LINZ / often required for Datafinder)
      - ".../services/wfs?key=XYZ" (some services accept; Datafinder in your test DID NOT)
    """
    base = (base or "").strip()
    if not base:
        raise ValueError("WFS base URL is empty")

    # If already includes ;key= then use as-is
    if ";key=" in base:
        return base.rstrip("?")

    if not key:
        return base.rstrip("?")

    # If it ends with /services/wfs, inject ;key= before /wfs
    # e.g. https://datafinder.stats.govt.nz/services/wfs  -> https://datafinder.stats.govt.nz/services;key=KEY/wfs
    if re.search(r"/services/wfs/?$", base):
        base2 = re.sub(r"/services/wfs/?$", f"/services;key={key}/wfs", base)
        return base2.rstrip("?")

    # Otherwise: fall back to query-param style
    return base.rstrip("?")  # we will pass key as query param when needed


def _wfs_get_capabilities(base: str, *, timeout: int = 60) -> str:
    """
    Returns XML text of GetCapabilities.
    """
    params = {"service": "WFS", "request": "GetCapabilities"}
    # Only add key as query param if base doesn't have ;key=
    if ";key=" not in base:
        key = _env("LINZ_KEY", "LINZ_API_KEY", "DATAFINDER_KEY", "DATAFINDER_API_KEY")
        if key:
            params["key"] = key

    r = requests.get(base, params=params, timeout=timeout)
    r.raise_for_status()
    return r.text


def _capabilities_featuretypenames(xml_text: str) -> list[str]:
    """
    Parse GetCapabilities and return a list of FeatureType Name values.
    """
    # WFS capabilities uses different namespaces; we just search for all <Name> under FeatureType.
    root = ET.fromstring(xml_text)

    names: list[str] = []
    # Look for any element that endswith 'FeatureType' then child that endswith 'Name'
    for ft in root.iter():
        if ft.tag.lower().endswith("featuretype"):
            for child in list(ft):
                if child.tag.lower().endswith("name") and child.text:
                    names.append(child.text.strip())
    # De-dup preserving order
    seen = set()
    out = []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _looks_like_exception_report(text: str) -> bool:
    t = (text or "")[:5000].lower()
    return ("exceptionreport" in t) or ("ows:exception" in t) or ("exceptiontext" in t)


def _wfs_getfeature(
    *,
    base: str,
    typename: str,
    bbox_4326: Optional[Tuple[float, float, float, float]],
    srs_name: str = "EPSG:4326",
    version: str = "2.0.0",
    timeout: int = 90,
) -> gpd.GeoDataFrame:
    """
    Fetch a GeoDataFrame from a WFS service using outputFormat JSON/GeoJSON.
    """
    params = {
        "service": "WFS",
        "version": version,
        "request": "GetFeature",
        "outputFormat": "application/json",
        "srsName": srs_name,
    }

    # WFS 2.0.0 uses typeNames; older uses typeName
    if str(version).startswith("2"):
        params["typeNames"] = typename
    else:
        params["typeName"] = typename

    # If base doesn't include ;key=, pass key as query param
    if ";key=" not in base:
        key = _env("LINZ_KEY", "LINZ_API_KEY", "DATAFINDER_KEY", "DATAFINDER_API_KEY")
        if key:
            params["key"] = key

    if bbox_4326 is not None:
        minx, miny, maxx, maxy = map(float, bbox_4326)
        # Many servers accept bbox=minx,miny,maxx,maxy,EPSG:4326
        params["bbox"] = f"{minx},{miny},{maxx},{maxy},{srs_name}"

    r = requests.get(base, params=params, timeout=timeout)
    r.raise_for_status()

    # Some services return 200 with ExceptionReport XML
    if _looks_like_exception_report(r.text):
        raise requests.HTTPError(f"WFS ExceptionReport from {r.url} :: {r.text[:300]}")

    return gpd.read_file(io.BytesIO(r.content))


def _pick_typename(
    *,
    layer_id: int,
    available_names: Iterable[str],
    preferred: Optional[str] = None,
) -> str:
    names = list(available_names)

    if preferred and preferred in names:
        return preferred

    # Common candidates
    candidates = [
        f"layer-{layer_id}",
        f"data.linz.govt.nz:layer-{layer_id}",
        f"datafinder.stats.govt.nz:layer-{layer_id}",
        f"stats.govt.nz:layer-{layer_id}",
        f"statsnz:layer-{layer_id}",
    ]
    for c in candidates:
        if c in names:
            return c

    # If nothing exact, allow substring match on layer id
    lid = f"layer-{layer_id}"
    for n in names:
        if lid in n:
            return n

    # Worst-case: try the plain layer-id
    return f"layer-{layer_id}"


# ----------------------------
# Public loaders
# ----------------------------
def load_lakes_and_rivers(
    *,
    bbox_4326: Optional[Tuple[float, float, float, float]] = None,
    local_folder: str | Path = "app/static/geodata",
    prefer_local: bool = True,
    debug: bool = False,
    # You can override IDs if you want different LINZ layers later:
    linz_lakes_layer_id: int = 50293,
    linz_rivers_layer_id: int = 50328,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Returns (lakes_gdf, rivers_gdf) in EPSG:4326 unless service returns otherwise.
    If prefer_local=True and geopackage/files exist, you can implement local caching later.
    """
    # Local caching hook (optional)
    # For now: always WFS if prefer_local is False; if prefer_local True but no local files, WFS.
    _ = Path(local_folder)

    linz_key = _env("LINZ_KEY", "LINZ_API_KEY")
    base = _wfs_base_with_key(_env("LINZ_WFS_BASE") or LINZ_WFS_BASE_DEFAULT, linz_key)

    if debug:
        print(f"[geo] LINZ base={base}")
        print(f"[geo] lakes layer={linz_lakes_layer_id} rivers layer={linz_rivers_layer_id}")
        print(f"[geo] bbox_4326={bbox_4326}")

    # Discover typenames once (fast enough)
    cap_xml = _wfs_get_capabilities(base)
    names = _capabilities_featuretypenames(cap_xml)

    lakes_typename = _pick_typename(layer_id=int(linz_lakes_layer_id), available_names=names)
    rivers_typename = _pick_typename(layer_id=int(linz_rivers_layer_id), available_names=names)

    # Fetch data
    lakes = _wfs_getfeature(
        base=base,
        typename=lakes_typename,
        bbox_4326=bbox_4326,
        srs_name="EPSG:4326",
        version="2.0.0",
    )
    rivers = _wfs_getfeature(
        base=base,
        typename=rivers_typename,
        bbox_4326=bbox_4326,
        srs_name="EPSG:4326",
        version="2.0.0",
    )

    if lakes.crs is None:
        lakes = lakes.set_crs(epsg=4326)
    if rivers.crs is None:
        rivers = rivers.set_crs(epsg=4326)

    return lakes, rivers


def load_regional_councils(
    *,
    debug: bool = False,
    debug_folder: str | Path | None = None,
    simplify_tol_deg: float = 0.0,
    use_internet: bool = True,
    datafinder_layer_id: int = 120945,
    bbox_4326: Optional[Tuple[float, float, float, float]] = None,
    datafinder_typename: Optional[str] = None,
) -> gpd.GeoDataFrame:
    """
    Stats NZ Datafinder: Regional council 2025 clipped (layer 120945).

    IMPORTANT:
    - Datafinder in your tests returns 401 if you use ?key=... query param.
    - So we build base as .../services;key=KEY/wfs (path style).
    """
    if not use_internet:
        raise RuntimeError("load_regional_councils(use_internet=False) not implemented (no local councils cache).")

    df_key = _env("DATAFINDER_KEY", "DATAFINDER_API_KEY")
    df_base_env = _env("DATAFINDER_WFS_BASE") or DATAFINDER_WFS_BASE_DEFAULT

    # Force ;key=.../wfs style if key exists
    base = _wfs_base_with_key(df_base_env, df_key)

    if debug:
        print(f"[geo] DATAFINDER base={base}")
        print(f"[geo] layer={datafinder_layer_id} bbox_4326={bbox_4326}")

    cap_xml = _wfs_get_capabilities(base)
    names = _capabilities_featuretypenames(cap_xml)

    typename = _pick_typename(layer_id=int(datafinder_layer_id), available_names=names, preferred=datafinder_typename)

    gdf = _wfs_getfeature(
        base=base,
        typename=typename,
        bbox_4326=bbox_4326,
        srs_name="EPSG:4326",
        version="2.0.0",
    )

    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)

    if simplify_tol_deg and float(simplify_tol_deg) > 0 and len(gdf):
        try:
            gdf = gdf.copy()
            gdf["geometry"] = gdf.geometry.simplify(float(simplify_tol_deg), preserve_topology=True)
        except Exception:
            pass

    return gdf