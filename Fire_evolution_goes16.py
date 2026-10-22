# EXPORT_TO_GITHUB
"""PyroCb: GOES-16/ABI Full Disk — fire, clouds, rainfall, phase.

Download NetCDFs one at a time to temporary DISK (never whole-file RAM),
read only the bbox, delete the source, retain small per-hour Parquet caches.
Public NOAA S3: no MAP_KEY, AWS account, or Earth Engine login.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import local
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np
import pandas as pd
import requests

PYROCB_VERSION = "2026-10-22-multiproduct-v3-arquivo-ncei"
BUCKET_URL = "https://noaa-goes16.s3.amazonaws.com"
_THREAD = local()
_GRID_CACHE = {}

# EXPORT_TO_GITHUB
# ============================================================
# 1. CONFIGURATION — CHANGE HERE
# ============================================================
DATA_INICIAL = "2019-08-17"
DATA_FINAL = "2019-08-19"        # inclusive (UTC)
BBOX = "-59.9,-19.8,-58.9,-18.8"
CODIGOS_FOGO = (10, 30)         # processed, conservative; 11/31 = saturated
PASSO_MINUTOS = 10              # can set 20/30/60 to speed up a trial
MAX_DOWNLOADS = 2               # bounded temporary disk/RAM
DQF_ACEITOS = (0,)              # highest quality level
INCLUIR_GELO_GRAFICO = False    # false = only requested liquid/supercooled/mixed
PASTA_DADOS = "/content/goes16_multi/dados"
PASTA_HISTORICOS = None  # pasta local com NetCDFs GOES-16 obtidos no NCEI/CLASS
FIG_DIR = Path("/content/goes16_multi/figures")
SAVE_FIGURES = True
OVERWRITE_FIGURES = True
FIG_DPI = 300
FIG_SIZE = (14, 17)

PRODUTOS = {  # Full Disk products, native resolutions (not resampled)
    "FDCF": "ABI-L2-FDCF",       # Mask, Power [MW], Area [m²]
    "ACHAF": "ABI-L2-ACHAF",     # HT [m] (nominal 10 km)
    "ACHTF": "ABI-L2-ACHTF",     # TEMP [K] (nominal 2 km)
    "CTPF": "ABI-L2-CTPF",       # PRES [hPa] (nominal 10 km)
    "RRQPEF": "ABI-L2-RRQPEF",   # RRQPE [mm/h] (nominal 2 km)
    "ACTPF": "ABI-L2-ACTPF",     # Phase: 1 water, 2 supercooled, 3 mixed, 4 ice
}
PHASES = {1: "liquid", 2: "supercooled", 3: "mixed", 4: "ice"}
PHASE_COLORS = {"liquid": "#388DC2", "supercooled": "#65B3A5",
                "mixed": "#9A74B5", "ice": "#DBAF59"}
MAIN_COLOR = "#2F5D50"

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "DejaVu Sans"],
    "font.size": 10, "axes.labelsize": 10, "xtick.labelsize": 9,
    "ytick.labelsize": 9, "axes.linewidth": 0.8,
    "axes.spines.top": False, "axes.spines.right": False,
    "savefig.dpi": FIG_DPI, "pdf.fonttype": 42,
})


def _garantir_dependencias():
    missing = [m for m in ("netCDF4", "pyproj", "pyarrow") if importlib.util.find_spec(m) is None]
    if missing:
        print("Instalando dependências:", missing)
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])


def _bbox_tuple(bbox):
    b = tuple(map(float, bbox.split(",") if isinstance(bbox, str) else bbox))
    if len(b) != 4 or not (-180 <= b[0] < b[2] <= 180 and -90 <= b[1] < b[3] <= 90):
        raise ValueError("BBOX: west,south,east,north")
    return b


def _cache_id(bbox, codigos, passo):
    cfg = [list(_bbox_tuple(bbox)), list(codigos), passo, DQF_ACEITOS,
           INCLUIR_GELO_GRAFICO, "v2"]
    return hashlib.sha1(json.dumps(cfg).encode()).hexdigest()[:12]


def _instante_arquivo(key):
    m = re.search(r"_s(\d{13,15})_e\d+_c\d+\.nc$", key)
    if not m:
        raise ValueError(f"Nome GOES inválido: {key}")
    return pd.Timestamp(datetime.strptime(m.group(1)[:13], "%Y%j%H%M%S"), tz="UTC").floor("10min")

# EXPORT_TO_GITHUB
# ============================================================
# 2. NOAA FILE LISTING AND STREAMING DOWNLOAD
# ============================================================
def _sessao():
    if not hasattr(_THREAD, "session"):
        _THREAD.session = requests.Session()
    return _THREAD.session


def _listar_dia(dia, produto):
    prefix = f"{PRODUTOS[produto]}/{dia.year}/{dia.timetuple().tm_yday:03d}/"
    token, found = None, {}
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": 1000}
        if token:
            params["continuation-token"] = token
        for retry in range(3):
            try:
                r = _sessao().get(BUCKET_URL, params=params, timeout=(15, 90))
                r.raise_for_status()
                break
            except requests.RequestException:
                if retry == 2:
                    raise
                time.sleep(retry + 1)
        root = ET.fromstring(r.content)
        for obj in root.findall("{*}Contents"):
            key = obj.findtext("{*}Key")
            if key and re.search(r"_s\d{13,15}_e\d+_c\d+\.nc$", key):
                t = _instante_arquivo(key)
                if t.date() == dia and (t not in found or key > found[t]):
                    found[t] = key
        if root.findtext("{*}IsTruncated", "false").lower() != "true":
            return found
        token = root.findtext("{*}NextContinuationToken")
        if not token:
            raise RuntimeError("S3 pagination token missing")


def _arquivos_historicos(pasta, produto, dias):
    """Index NetCDFs from NCEI/CLASS, even in nested folders, for required dates.

    The originals remain on disk: this code only reads their bbox on demand.
    No network access or recopying required.
    """
    if not pasta:
        return {}
    raiz = Path(pasta).expanduser()
    if not raiz.is_dir():
        raise FileNotFoundError(f"Pasta histórica não encontrada: {raiz}")
    wanted = set(dias)
    files = {}
    for path in raiz.rglob("*.nc"):
        name = path.name
        if f"ABI-L2-{produto}-" not in name or "_G16_" not in name:
            continue
        try:
            t = _instante_arquivo(name)
        except ValueError:
            continue
        if t.date() in wanted and (t not in files or name > files[t].name):
            files[t] = path.resolve()
    return files


def _baixar_temporario(key, folder):
    """Stream S3 or read already-downloaded historical NetCDF without copying."""
    if isinstance(key, Path) and key.is_file():
        return key
    path = Path(folder) / Path(key).name
    for retry in range(3):
        try:
            with _sessao().get(f"{BUCKET_URL}/{key}", stream=True,
                               timeout=(15, 120)) as r:
                r.raise_for_status()
                with path.open("wb") as fp:
                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            fp.write(chunk)
            with path.open("rb") as fp:
                if fp.read(8) != b"\x89HDF\r\n\x1a\n":
                    raise OSError("Invalid HDF5/NetCDF4 response")
            return path
        except (requests.RequestException, OSError):
            path.unlink(missing_ok=True)
            if retry == 2:
                raise
            time.sleep(1.5 * (retry + 1))

# EXPORT_TO_GITHUB
# ============================================================
# 3. REGIONAL CROP, NATIVE GRID AND QUALITY CONTROL
# ============================================================
def _recorte(ds, bbox):
    """Geostationary grid → tight x/y slice + exact geographic pixel mask."""
    from pyproj import CRS, Transformer
    p = ds.variables["goes_imager_projection"]
    h = float(p.perspective_point_height)
    x = np.asarray(ds.variables["x"][:], dtype=float) * h
    y = np.asarray(ds.variables["y"][:], dtype=float) * h
    chave = (x.size, y.size, round(x[0]), round(x[-1]),
             round(y[0]), round(y[-1]), float(p.longitude_of_projection_origin),
             _bbox_tuple(bbox))
    if chave in _GRID_CACHE:
        return _GRID_CACHE[chave]
    crs = CRS.from_proj4(
        f"+proj=geos +h={h} +lon_0={float(p.longitude_of_projection_origin)} "
        f"+a={float(p.semi_major_axis)} +b={float(p.semi_minor_axis)} "
        f"+sweep={p.sweep_angle_axis} +units=m +no_defs"
    )
    forward = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    inverse = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    w, s, e, n = _bbox_tuple(bbox)
    lx = np.linspace(w, e, 21)
    ly = np.linspace(s, n, 21)
    lon_edge = np.r_[lx, lx, np.full(21, w), np.full(21, e)]
    lat_edge = np.r_[np.full(21, s), np.full(21, n), ly, ly]
    xx, yy = forward.transform(lon_edge, lat_edge)
    valid = np.isfinite(xx) & np.isfinite(yy)
    if not valid.any():
        raise ValueError("BBOX não está no disco do GOES-16")
    dx = 2 * abs(x[1]-x[0]) if x.size > 1 else 5000
    dy = 2 * abs(y[1]-y[0]) if y.size > 1 else 5000
    ix = np.flatnonzero((x >= np.min(xx[valid])-dx) & (x <= np.max(xx[valid])+dx))
    iy = np.flatnonzero((y >= np.min(yy[valid])-dy) & (y <= np.max(yy[valid])+dy))
    if not ix.size or not iy.size:
        raise ValueError("Sem pixels da grade dentro da BBOX")
    xs, ys = slice(ix[0], ix[-1]+1), slice(iy[0], iy[-1]+1)
    gx, gy = np.meshgrid(x[xs], y[ys])
    lon, lat = inverse.transform(gx, gy)
    dentro = ((lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
              & np.isfinite(lon) & np.isfinite(lat))
    if len(_GRID_CACHE) > 15:
        _GRID_CACHE.clear()
    _GRID_CACHE[chave] = ys, xs, dentro, lon, lat
    return _GRID_CACHE[chave]


def _valores(ds, campo, ys, xs, dentro):
    """netCDF4 transparently unpacks scale/offset and masks fill values."""
    if campo not in ds.variables:
        raise KeyError(f"Variável ausente no NetCDF: {campo}")
    values = np.ma.filled(ds.variables[campo][ys, xs], np.nan).astype(float)
    good = dentro & np.isfinite(values)
    if "DQF" in ds.variables:
        dqf = np.ma.filled(ds.variables["DQF"][ys, xs], -9999)
        good &= np.isin(dqf, DQF_ACEITOS)
    return values, good


def _extrair_cena(path, produto, t, bbox, codigos):
    """Returns 1 reduced temporal row + optional few fire pixels."""
    from netCDF4 import Dataset
    row = {"time_utc": t, "disponivel": True}
    fires = None
    with Dataset(str(path)) as ds:
        ys, xs, inside, lon, lat = _recorte(ds, bbox)
        if produto == "FDCF":
            mask = np.ma.filled(ds.variables["Mask"][ys, xs], -9999)
            fogo = inside & np.isin(mask, codigos)
            n = int(fogo.sum())
            row["focos"] = n
            if n:
                frp = np.ma.filled(ds.variables["Power"][ys, xs], np.nan).astype(float)[fogo]
                area = np.ma.filled(ds.variables["Area"][ys, xs], np.nan).astype(float)[fogo]
                frp[~np.isfinite(frp) | (frp < 0)] = np.nan
                area[~np.isfinite(area) | (area < 0)] = np.nan
                fires = pd.DataFrame({
                    "time_utc": t, "latitude": lat[fogo], "longitude": lon[fogo],
                    "mask_code": mask[fogo].astype("int16"), "frp_mw": frp,
                    "fire_area_m2": area, "fire_area_km2": area/1e6,
                })
            else:
                frp, area = np.array([]), np.array([])
            row["n_frp_validos"] = int(np.isfinite(frp).sum())
            row["frp_mean_mw"] = float(np.nanmean(frp)) if np.isfinite(frp).any() else np.nan
            row["frp_max_mw"] = float(np.nanmax(frp)) if np.isfinite(frp).any() else np.nan
            row["frp_sum_mw"] = (float(np.nansum(frp)) if np.isfinite(frp).any() else
                                 (0.0 if n == 0 else np.nan))
            row["fire_area_sum_m2"] = (float(np.nansum(area)) if np.isfinite(area).any() else
                                      (0.0 if n == 0 else np.nan))
            row["fire_area_sum_km2"] = row["fire_area_sum_m2"] / 1e6
        elif produto == "ACTPF":
            v, good = _valores(ds, "Phase", ys, xs, inside)
            for k, name in PHASES.items():
                row[f"phase_{name}_count"] = int(np.sum(good & (v == k)))
            row["phase_clear_count"] = int(np.sum(good & (v == 0)))
            row["phase_unknown_count"] = int(np.sum(good & (v == 5)))
        else:
            variable = {"ACHAF": "HT", "ACHTF": "TEMP",
                        "CTPF": "PRES", "RRQPEF": "RRQPE"}[produto]
            arr, good = _valores(ds, variable, ys, xs, inside)
            values = arr[good]
            stat = float(np.max(values)) if len(values) and produto in ("ACHAF", "RRQPEF") else (
                float(np.min(values)) if len(values) else np.nan)
            out_name = {"ACHAF": "cloud_top_height_max_km",
                        "ACHTF": "cloud_top_temp_min_c",
                        "CTPF": "cloud_top_pressure_min_hpa",
                        "RRQPEF": "rain_rate_max_mm_h"}[produto]
            row[out_name] = stat / 1000 if produto == "ACHAF" else (
                stat - 273.15 if produto == "ACHTF" else stat)
    return row, fires

# EXPORT_TO_GITHUB
# ============================================================
# 4. PARQUET CACHE BY PRODUCT AND HOUR (RESTART SAFE)
# ============================================================
FIRE_COLS = ["time_utc", "latitude", "longitude", "mask_code", "frp_mw",
             "fire_area_m2", "fire_area_km2"]
DATA_COLS = [
    "focos", "n_frp_validos", "frp_mean_mw", "frp_max_mw", "frp_sum_mw",
    "fire_area_sum_m2", "fire_area_sum_km2", "cloud_top_height_max_km",
    "cloud_top_temp_min_c", "cloud_top_pressure_min_hpa", "rain_rate_max_mm_h",
    "phase_liquid_count", "phase_supercooled_count", "phase_mixed_count",
    "phase_ice_count", "phase_clear_count", "phase_unknown_count",
]


def _hour_path(base, produto, hora, kind="stats"):
    parent = base / produto
    parent.mkdir(exist_ok=True, parents=True)
    return parent / f"{hora:%Y%m%d_%H}_{kind}.parquet"


def _atomic_parquet(df, path):
    tmp = path.with_suffix(".part.parquet")
    df.to_parquet(tmp, compression="zstd", index=False)
    tmp.replace(path)


def _process_hour(base, produto, hora, scenes, bbox, codigos, pool):
    rows, pixels = [], []
    if scenes:
        with TemporaryDirectory(prefix="goes16_") as temp:
            pairs = sorted(scenes.items())
            # max 6 outputs/hour, and at most MAX_DOWNLOADS concurrent downloads.
            paths = pool.map(lambda item: _baixar_temporario(item[1], temp), pairs)
            for (time_, key), path in zip(pairs, paths):
                try:
                    row, df = _extrair_cena(path, produto, time_, bbox, codigos)
                    rows.append(row)
                    if df is not None:
                        pixels.append(df)
                finally:
                    if not isinstance(key, Path):
                        path.unlink(missing_ok=True)
    if produto == "FDCF":
        fire_df = pd.concat(pixels, ignore_index=True) if pixels else pd.DataFrame(columns=FIRE_COLS)
        _atomic_parquet(fire_df, _hour_path(base, produto, hora, "pixels"))
    stats_df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["time_utc"])
    _atomic_parquet(stats_df, _hour_path(base, produto, hora))


def baixar_e_processar_goes(data_inicial=None, data_final=None, bbox=None,
                           codigos_fogo=None, pasta=None, rebaixar=False,
                           max_downloads=None, passo_minutos=None,
                           historicos=None):
    """Use NOAA S3 plus optional local NCEI/CLASS NetCDF historical archive."""
    start = date.fromisoformat(str(data_inicial or DATA_INICIAL))
    end = date.fromisoformat(str(data_final or DATA_FINAL))
    if start > end:
        raise ValueError("A data inicial deve ser <= data final")
    area = _bbox_tuple(BBOX if bbox is None else bbox)
    codes = tuple(int(c) for c in (CODIGOS_FOGO if codigos_fogo is None else codigos_fogo))
    step = int(PASSO_MINUTOS if passo_minutos is None else passo_minutos)
    if step not in (10, 20, 30, 60):
        raise ValueError("passo_minutos: usar 10, 20, 30 ou 60")
    workers = int(MAX_DOWNLOADS if max_downloads is None else max_downloads)
    if not 1 <= workers <= 4:
        raise ValueError("max_downloads deve ser 1..4")
    root = Path(PASTA_DADOS if pasta is None else pasta)
    archive = PASTA_HISTORICOS if historicos is None else historicos
    root.mkdir(parents=True, exist_ok=True)
    _garantir_dependencias()
    days = [start + timedelta(days=i) for i in range((end-start).days+1)]
    hours = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=h)
             for d in days for h in range(24)]
    key = _cache_id(area, codes, step)
    cache = root / f"cache_horario_{key}"
    cache.mkdir(exist_ok=True)
    outfile = root / f"GOES16_{start}_{end}_{key}"
    f_series = Path(f"{outfile}_serie.parquet")
    f_pixels = Path(f"{outfile}_fogos.parquet")
    f_coverage = Path(f"{outfile}_cobertura.parquet")
    if not rebaixar and not archive and all(f.exists() for f in (f_series, f_pixels, f_coverage)):
        print("Cache final encontrado:", f_series)
        return {"df_goes": pd.read_parquet(f_pixels),
                "serie_temporal": pd.read_parquet(f_series),
                "cobertura_produtos": pd.read_parquet(f_coverage),
                "bbox": area, "inicio": start, "fim": end,
                "arquivo": f_pixels, "arquivo_serie": f_series}

    catalogs = {}
    for product in PRODUTOS:
        local_files = _arquivos_historicos(archive, product, days)
        local_files = {t: path for t, path in local_files.items()
                       if (t.hour*60 + t.minute) % step == 0}
        uncached_hours = {h for h in hours
                          if rebaixar or not _hour_path(cache, product, h).exists()}
        # The old run may have written an empty hourly cache. Only reopen
        # those hours if actual historical files have since been supplied.
        for t in local_files:
            h = t.floor("h")
            if h not in uncached_hours and pd.read_parquet(_hour_path(cache, product, h)).empty:
                uncached_hours.add(h)
        if not uncached_hours:
            print(f"{product}: 100% em cache")
            continue
        product_files = {}
        # If local archive covers a day completely for selected time slots,
        # skip the NOAA catalog request; otherwise fill gaps from NOAA.
        for day in days:
            local_day = {t: p for t, p in local_files.items() if t.date() == day}
            if any(h.date() == day for h in uncached_hours):
                product_files.update(_listar_dia(day, product))
        product_files = {t: key for t, key in product_files.items()
                         if (t.hour*60 + t.minute) % step == 0}
        product_files.update(local_files)  # local NetCDF preferred when present
        catalogs[product] = {}
        for t, key in product_files.items():
            h = t.floor("h")
            if h in uncached_hours:
                catalogs[product].setdefault(h, {})[t] = key
        print(f"{product}: {len(product_files)} cenas, "
              f"{len(local_files)} arquivos históricos locais")
        if not product_files:
            print(f"  AVISO: sem arquivos na AWS ou no arquivo histórico informado.")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        n_total, n_done = len(hours)*len(PRODUTOS), 0
        for product in PRODUTOS:
            for hour in hours:
                n_done += 1
                target = _hour_path(cache, product, hour)
                if target.exists() and not rebaixar and hour not in catalogs.get(product, {}):
                    continue
                scenes = catalogs.get(product, {}).get(hour, {})
                if scenes:
                    print(f"\r{product} | {hour:%d/%m %H} UTC | {n_done}/{n_total}",
                          end="", flush=True)
                _process_hour(cache, product, hour, scenes, area, codes, pool)
    print()

    timeline = pd.date_range(pd.Timestamp(start, tz="UTC"),
                             pd.Timestamp(end+timedelta(days=1), tz="UTC"),
                             freq="10min", inclusive="left")
    series = pd.DataFrame(index=timeline)
    series.index.name = "time_utc"
    fire_parts, report = [], []
    for product in PRODUTOS:
        stats = []
        for hour in hours:
            part = pd.read_parquet(_hour_path(cache, product, hour))
            if len(part):
                stats.append(part)
            if product == "FDCF":
                pixels = pd.read_parquet(_hour_path(cache, product, hour, "pixels"))
                if len(pixels):
                    fire_parts.append(pixels)
        availability = f"disponivel_{product}"
        if stats:
            tbl = pd.concat(stats, ignore_index=True)
            tbl["time_utc"] = pd.to_datetime(tbl["time_utc"], utc=True)
            tbl = tbl.sort_values("time_utc").drop_duplicates("time_utc", keep="last")
            tbl = tbl.set_index("time_utc").rename(columns={"disponivel": availability})
            series = series.join(tbl, how="left")
        else:
            series[availability] = False
        series[availability] = series[availability].eq(True)
        report.append({"produto": product, "cenas_disponiveis": int(series[availability].sum()),
                       "slots_10min": len(series)})

    for col in DATA_COLS:
        if col not in series:
            series[col] = np.nan
    series["cena_disponivel"] = series["disponivel_FDCF"]  # backward compatibility
    three = (series["phase_liquid_count"].fillna(0)
             + series["phase_supercooled_count"].fillna(0)
             + series["phase_mixed_count"].fillna(0))
    four = three + series["phase_ice_count"].fillna(0)
    series["phase_selected_count"] = three.where(series["disponivel_ACTPF"])
    for phase in PHASES.values():
        # Full composition is always retained (including ice).
        series[f"phase_{phase}_pct_all"] = (
            100 * series[f"phase_{phase}_count"] / four
        ).where(four > 0)
        # Chart composition is normalised only to chosen displayed classes.
        denominator = four if INCLUIR_GELO_GRAFICO else three
        if phase == "ice" and not INCLUIR_GELO_GRAFICO:
            series[f"phase_{phase}_pct"] = series[f"phase_{phase}_pct_all"]
        else:
            series[f"phase_{phase}_pct"] = (
                100 * series[f"phase_{phase}_count"] / denominator
            ).where(denominator > 0)
    series = series.reset_index()
    fires = (pd.concat(fire_parts, ignore_index=True) if fire_parts
             else pd.DataFrame(columns=FIRE_COLS))
    coverage = pd.DataFrame(report)
    print("\nCobertura NOAA por produto:")
    print(coverage.to_string(index=False))
    print(f"Pixels de fogo na área: {len(fires)}")
    _atomic_parquet(series, f_series)
    _atomic_parquet(fires, f_pixels)
    _atomic_parquet(coverage, f_coverage)
    return {"df_goes": fires, "serie_temporal": series,
            "cobertura_produtos": coverage, "bbox": area,
            "inicio": start, "fim": end,
            "arquivo": f_pixels, "arquivo_serie": f_series}

# EXPORT_TO_GITHUB
# ============================================================
# 5. ONE FIGURE: EIGHT PANELS, INCLUDING 100% PHASE BARS
# ============================================================
def gerar_figura_goes(serie, inicio, fim, bbox, mostrar=True, codigos=None, passo=None):
    times = pd.to_datetime(serie["time_utc"], utc=True).dt.tz_localize(None)
    fig, axes = plt.subplots(8, 1, sharex=True, figsize=FIG_SIZE,
                             layout="constrained", gridspec_kw={"hspace": 0.15})
    panels = [
        ("frp_mean_mw", "Mean fire radiative power (MW)"),
        ("frp_sum_mw", "Total fire radiative power (MW)"),
        ("fire_area_sum_km2", "Total active-fire area (km²)"),
        ("cloud_top_height_max_km", "Maximum cloud-top height (km)"),
        ("cloud_top_temp_min_c", "Minimum cloud-top temperature (°C)"),
        ("cloud_top_pressure_min_hpa", "Minimum cloud-top pressure (hPa)"),
        ("rain_rate_max_mm_h", "Maximum rainfall rate (mm h⁻¹)"),
    ]
    for ax, (column, ylabel) in zip(axes[:7], panels):
        y = pd.to_numeric(serie[column], errors="coerce")
        ax.plot(times, y, lw=1.5, c=MAIN_COLOR, marker="o", ms=1.4, mew=0)
        ax.set_ylabel(ylabel, labelpad=8)
        ax.yaxis.set_major_locator(ticker.MaxNLocator(nbins=4))
        ax.grid(axis="y", linewidth=0.45, c="#D4D4D4")
        if not y.notna().any():
            ax.text(0.5, 0.5, "No valid data for this period", color="#888888",
                    transform=ax.transAxes, ha="center", va="center", fontsize=9)
        elif column in ("frp_mean_mw", "frp_sum_mw", "fire_area_sum_km2",
                        "rain_rate_max_mm_h"):
            ax.set_ylim(bottom=0)

    phase_ax = axes[7]
    names = ["liquid", "supercooled", "mixed"]
    if INCLUIR_GELO_GRAFICO:
        names.append("ice")
    labels = {"liquid": "Liquid water", "supercooled": "Supercooled liquid",
              "mixed": "Mixed phase", "ice": "Ice"}
    base = np.zeros(len(times), dtype=float)
    for name in names:
        pct = pd.to_numeric(serie[f"phase_{name}_pct"], errors="coerce").to_numpy()
        pct = np.where(np.isfinite(pct), pct, 0)
        phase_ax.bar(times, pct, bottom=base, width=8/(24*60),
                     color=PHASE_COLORS[name], label=labels[name], linewidth=0)
        base += pct
    phase_ax.set_ylim(0, 100)
    phase_ax.set_yticks([0, 25, 50, 75, 100])
    phase_ax.set_ylabel("Cloud-top phase (%)", labelpad=8)
    phase_ax.grid(axis="y", c="#D4D4D4", linewidth=0.45)
    phase_ax.set_axisbelow(True)
    if base.sum() == 0:
        phase_ax.text(0.5, 0.5, "No pixels in the chosen phase categories",
                      transform=phase_ax.transAxes, ha="center", va="center")
    phase_ax.legend(loc="upper center", bbox_to_anchor=(0.5, 1.32), ncol=4,
                    frameon=False, fontsize=9)
    left = pd.Timestamp(inicio)
    right = pd.Timestamp(fim) + pd.Timedelta(days=1)
    phase_ax.set_xlim(left, right)
    phase_ax.set_xlabel("Acquisition date and time (UTC)")
    phase_ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 6, 12, 18]))
    phase_ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    for ax in axes:
        for d in pd.date_range(left + pd.Timedelta(days=1), pd.Timestamp(fim), freq="D"):
            ax.axvline(d, c="#BABABA", lw=0.7, zorder=0)
    w, s, e, n = _bbox_tuple(bbox)
    fig.suptitle(f"GOES-16 | {inicio} to {fim} | BBOX {w:g}, {s:g}, {e:g}, {n:g}",
                 fontsize=13, fontweight="semibold")
    if not INCLUIR_GELO_GRAFICO:
        fig.text(0.5, 0.001, "Phase percentages normalised to three requested classes (ice excluded).",
                 ha="center", fontsize=8, color="#777777")
    if SAVE_FIGURES:
        FIG_DIR.mkdir(parents=True, exist_ok=True)
        stem = f"goes16_multivariable_{inicio}_{fim}_{_cache_id(bbox, codigos or CODIGOS_FOGO, passo or PASSO_MINUTOS)}"
        for ext in ("png", "pdf"):
            target = FIG_DIR / f"{stem}.{ext}"
            if OVERWRITE_FIGURES or not target.exists():
                fig.savefig(target, dpi=FIG_DPI, facecolor="white", bbox_inches="tight")
                print("Figura salva:", target)
    if mostrar:
        plt.show()
    else:
        plt.close(fig)
    return fig

# EXPORT_TO_GITHUB
# ============================================================
# 6. PUBLIC INTERFACE — CALL FROM ANOTHER GOOGLE COLAB
# ============================================================
def baixar_goes(data_inicial=None, data_final=None, bbox=None,
                codigos_fogo=None, pasta=None, rebaixar=False, mostrar=True,
                max_downloads=None, passo_minutos=None,
                historicos=None):
    """Exactly one 8-panel plot; optionally read local NCEI/CLASS archive."""
    print("PyroCb GOES-16:", PYROCB_VERSION)
    out = baixar_e_processar_goes(
        data_inicial=data_inicial, data_final=data_final, bbox=bbox,
        codigos_fogo=codigos_fogo, pasta=pasta, rebaixar=rebaixar,
        max_downloads=max_downloads, passo_minutos=passo_minutos,
        historicos=historicos,
    )
    fig = gerar_figura_goes(
        out["serie_temporal"], out["inicio"], out["fim"], out["bbox"],
        mostrar=mostrar,
        codigos=CODIGOS_FOGO if codigos_fogo is None else codigos_fogo,
        passo=PASSO_MINUTOS if passo_minutos is None else passo_minutos,
    )
    out["fig_resumo"] = fig
    out["fig_frp_media"] = fig  # backwards compatible key (now full summary)
    return out
