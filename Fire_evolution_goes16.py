# EXPORT_TO_GITHUB
"""PyroCb | GOES-16 ABI L2 FDCF | evolução temporal de FRP.

Fonte: fire_evolution_goes16.ipynb. Não requer NASA FIRMS MAP_KEY.
Os NetCDF originais são lidos temporariamente em memória (lotes pequenos),
recortados espacialmente e descartados; somente os Parquet são persistidos.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import local
import importlib.util
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import requests

PYROCB_VERSION = "2026-10-22-goes16-fdcf-v1"
BUCKET_URL = "https://noaa-goes16.s3.amazonaws.com"
_THREAD = local()  # one persistent HTTP session per download worker
CACHE_VERSION = "v1"

# EXPORT_TO_GITHUB
# ============================================================
# 1. PARAMETERS — EDIT ONLY IN THE SOURCE NOTEBOOK
# ============================================================
DATA_INICIAL = "2019-08-17"
DATA_FINAL = "2019-08-19"  # inclusive
BBOX = "-62.5,-21,-57,-17"  # west, south, east, north (same as MODIS)

# NOAA: 10,30 = processed fire pixels, conservative, most reliable FRP.
# 11,31 = saturated (FRP is a lower-bound estimate); add if desired.
CODIGOS_FOGO = (10, 30)

TIME_BIN_MINUTES = 10
MAX_DOWNLOADS = 5  # limits RAM and simultaneous HTTP requests
PASTA_DADOS = "/content/goes16_fdcf/dados"
FIG_DIR = Path("/content/goes16_fdcf/figures")

SAVE_FIGURES = True
OVERWRITE_FIGURES = True
FIG_DPI = 450
FIG_SIZE = (183 / 25.4, 94 / 25.4)
COLORS = {
    "green": "#009E73",
    "black": "#222222",
    "gray": "#737373",
}

mpl.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
    "font.size": 7,
    "axes.labelsize": 7,
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
    "axes.linewidth": 0.75,
    "axes.edgecolor": COLORS["black"],
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "figure.dpi": 140,
    "savefig.dpi": FIG_DPI,
})


def _garantir_dependencias():
    """The main Colab only needs to call baixar_goes()."""
    missing = [p for p in ("netCDF4", "pyproj", "pyarrow")
               if importlib.util.find_spec(p) is None]
    if missing:
        print("Instalando dependências:", ", ".join(missing))
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *missing])


def _bbox_tuple(bbox):
    vals = [float(v) for v in bbox.split(",")] if isinstance(bbox, str) else [float(v) for v in bbox]
    if len(vals) != 4 or not (-180 <= vals[0] < vals[2] <= 180) or not (-90 <= vals[1] < vals[3] <= 90):
        raise ValueError("BBOX precisa ser (west,south,east,north), sem cruzar ±180°.")
    return tuple(vals)


def _id_area(bbox):
    return "_".join(f"{v:g}".replace("-", "m").replace(".", "p")
                    for v in _bbox_tuple(bbox))


def _base_cache(inicio, fim, bbox, codigos):
    codes = "-".join(map(str, codigos))
    return f"GOES16_FDCF_{inicio}_{fim}_{_id_area(bbox)}_mask{codes}_{CACHE_VERSION}"


# EXPORT_TO_GITHUB
# ============================================================
# 2. LIST PUBLIC NOAA OBJECTS — ONLY THREE SMALL S3 LISTINGS
# ============================================================
_PATTERN = re.compile(r"_s(\d{13,14})_e\d+_c\d+\.nc$")


def _instante_arquivo(key):
    """Acquisition start time from GOES filename, normalized to 10 min (UTC)."""
    match = _PATTERN.search(key)
    if not match:
        raise ValueError(f"Nome GOES não reconhecido: {key}")
    t = datetime.strptime(match.group(1)[:13], "%Y%j%H%M%S")
    return pd.Timestamp(t, tz="UTC").floor(f"{TIME_BIN_MINUTES}min")


def _listar_dia(dia, session=None):
    """Anonymous HTTP listing: no AWS credentials and no API key."""
    prefix = f"ABI-L2-FDCF/{dia.year}/{dia.timetuple().tm_yday:03d}/"
    keys = []
    token = None
    sess = session or requests
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": 1000}
        if token:
            params["continuation-token"] = token
        r = sess.get(BUCKET_URL, params=params, timeout=(15, 90))
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ns = "{http://s3.amazonaws.com/doc/2006-03-01/}"
        for e in root.findall(f"{ns}Contents"):
            elem = e.find(f"{ns}Key")
            if elem is not None and elem.text and _PATTERN.search(elem.text):
                keys.append(elem.text)
        truncated = root.findtext(f"{ns}IsTruncated", "false").lower() == "true"
        if not truncated:
            break
        token = root.findtext(f"{ns}NextContinuationToken")
        if not token:
            raise RuntimeError("S3 indicou outra página mas não retornou token.")

    # Rare duplicated observations: keep latest processed product (by creation suffix).
    selected = {}
    for key in sorted(keys):
        stamp = _instante_arquivo(key)
        if stamp.date() == dia:
            selected[stamp] = key
    return selected


def _baixar_bytes(key):
    """Bounded retries; NetCDF is NOT retained on disk."""
    # Key only contains valid S3 file names and '/' path segments.
    url = f"{BUCKET_URL}/{key}"
    if not hasattr(_THREAD, "session"):
        _THREAD.session = requests.Session()
    for tentativas in range(3):
        try:
            r = _THREAD.session.get(url, timeout=(15, 120))
            r.raise_for_status()
            raw = r.content
            if not raw.startswith(b"\x89HDF\r\n\x1a\n"):
                raise OSError(f"Conteúdo não parece NetCDF4/HDF5: {key}")
            return raw
        except (requests.RequestException, OSError):
            if tentativas == 2:
                raise
            time.sleep(1.5 * (tentativas + 1))


# EXPORT_TO_GITHUB
# ============================================================
# 3. PROCESS ONLY THE REGION OF INTEREST (NO FULL-DISK ARRAY)
# ============================================================
_COLUNAS = ["time_utc", "latitude", "longitude", "mask_code", "frp_mw"]


def _extrair_cena(raw, instante, bbox, codigos):
    """Read only small x/y slices from one in-memory NetCDF4 scene.

    netCDF4/HDF5 reads occur in the main thread (no unsafe parallel HDF5 access).
    Latitude/longitude transformations run only on candidate fire pixels.
    """
    from netCDF4 import Dataset
    from pyproj import CRS, Transformer

    oeste, sul, leste, norte = bbox
    with Dataset("cena_fdcf.nc", mode="r", memory=raw) as ds:
        p = ds.variables["goes_imager_projection"]
        h = float(p.perspective_point_height)
        geos = CRS.from_proj4(
            f"+proj=geos +h={h} +lon_0={float(p.longitude_of_projection_origin)} "
            f"+a={float(p.semi_major_axis)} +b={float(p.semi_minor_axis)} "
            f"+sweep={p.sweep_angle_axis} +units=m +no_defs"
        )
        forward = Transformer.from_crs("EPSG:4326", geos, always_xy=True)
        inverse = Transformer.from_crs(geos, "EPSG:4326", always_xy=True)

        # Perimeter, rather than only four corners, provides a robust projected crop.
        lin_lon = np.linspace(oeste, leste, 9)
        lin_lat = np.linspace(sul, norte, 9)
        lon_per = np.r_[lin_lon, lin_lon, np.full(9, oeste), np.full(9, leste)]
        lat_per = np.r_[np.full(9, sul), np.full(9, norte), lin_lat, lin_lat]
        xp, yp = forward.transform(lon_per, lat_per)
        valid = np.isfinite(xp) & np.isfinite(yp)
        if not valid.any():
            raise ValueError("BBOX fora do disco visível do GOES-16")

        x = np.asarray(ds.variables["x"][:], dtype=np.float64) * h
        y = np.asarray(ds.variables["y"][:], dtype=np.float64) * h
        margem_m = 5000.0
        ix = np.flatnonzero((x >= np.min(xp[valid]) - margem_m) &
                            (x <= np.max(xp[valid]) + margem_m))
        iy = np.flatnonzero((y >= np.min(yp[valid]) - margem_m) &
                            (y <= np.max(yp[valid]) + margem_m))
        if ix.size == 0 or iy.size == 0:
            return pd.DataFrame([[instante, np.nan, np.nan, np.nan, np.nan]], columns=_COLUNAS)

        xs = slice(int(ix[0]), int(ix[-1]) + 1)
        ys = slice(int(iy[0]), int(iy[-1]) + 1)
        mask = np.ma.filled(ds.variables["Mask"][ys, xs].astype("int16"), -9999)
        selecionado = np.isin(mask, codigos)
        rr, cc = np.nonzero(selecionado)
        if rr.size == 0:
            return pd.DataFrame([[instante, np.nan, np.nan, np.nan, np.nan]], columns=_COLUNAS)

        xx = x[ix[0] + cc]
        yy = y[iy[0] + rr]
        lon, lat = inverse.transform(xx, yy)
        dentro = ((lon >= oeste) & (lon <= leste) &
                  (lat >= sul) & (lat <= norte) &
                  np.isfinite(lon) & np.isfinite(lat))
        if not dentro.any():
            return pd.DataFrame([[instante, np.nan, np.nan, np.nan, np.nan]], columns=_COLUNAS)

        # Read FRP only when fire candidates exist in the crop.
        power = np.ma.filled(ds.variables["Power"][ys, xs].astype("float64"), np.nan)
        frp = power[rr[dentro], cc[dentro]]
        frp = np.where(np.isfinite(frp) & (frp >= 0), frp, np.nan)
        return pd.DataFrame({
            "time_utc": instante,
            "latitude": lat[dentro],
            "longitude": lon[dentro],
            "mask_code": mask[rr[dentro], cc[dentro]].astype(int),
            "frp_mw": frp,
        })


def _processar_hora(cenas, bbox, codigos, destino, pool):
    """Up to six scenes/hour: parallel HTTP, serialized small NetCDF reads.

    The file is atomic: an interrupted hour is not considered cached.
    """
    registros = []
    # Reuse HTTP connections across hours; at most six source files in RAM.
    for instante, raw in zip(cenas.keys(), pool.map(_baixar_bytes, cenas.values())):
        registros.append(_extrair_cena(raw, instante, bbox, codigos))
    df = pd.concat(registros, ignore_index=True)
    tmp = destino.with_suffix(".tmp.parquet")
    df.to_parquet(tmp, engine="pyarrow", compression="zstd", index=False)
    tmp.replace(destino)
    return df


# EXPORT_TO_GITHUB
# ============================================================
# 4. DATAFRAMES + PARQUET CACHE
# ============================================================
def baixar_e_processar_goes(
    data_inicial=None, data_final=None, bbox=None, codigos_fogo=None,
    pasta=None, rebaixar=False, max_downloads=None,
):
    """Return individual fire pixels and scan-wise mean FRP at 10-min resolution.

    Cache is grouped by hour for interruption recovery, plus a compact final cache.
    No source NetCDF is saved. Missing scenes remain missing (not zero).
    """
    ini = date.fromisoformat(str(data_inicial or DATA_INICIAL))
    fim = date.fromisoformat(str(data_final or DATA_FINAL))
    if ini > fim:
        raise ValueError("DATA_INICIAL deve ser <= DATA_FINAL")
    area = _bbox_tuple(BBOX if bbox is None else bbox)
    codes = tuple(int(c) for c in (CODIGOS_FOGO if codigos_fogo is None else codigos_fogo))
    if not codes:
        raise ValueError("Defina ao menos um código de Mask para fogo.")
    max_workers = int(MAX_DOWNLOADS if max_downloads is None else max_downloads)
    if not 1 <= max_workers <= 8:
        raise ValueError("max_downloads deve ser 1..8 para controlar RAM.")
    pasta = Path(PASTA_DADOS if pasta is None else pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    base = _base_cache(ini.isoformat(), fim.isoformat(), area, codes)
    fire_file = pasta / f"{base}_fogos.parquet"
    series_file = pasta / f"{base}_serie.parquet"
    _garantir_dependencias()

    if fire_file.exists() and series_file.exists() and not rebaixar:
        print(f"Reutilizando cache GOES-16: {fire_file}")
        return {
            "df_goes": pd.read_parquet(fire_file),
            "serie_temporal": pd.read_parquet(series_file),
            "arquivo": fire_file, "arquivo_serie": series_file,
            "bbox": area, "inicio": ini, "fim": fim,
        }

    dias = [ini + timedelta(days=j) for j in range((fim - ini).days + 1)]
    horas = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=h)
             for d in dias for h in range(24)]
    hour_cache = pasta / "cache_horario"
    hour_cache.mkdir(parents=True, exist_ok=True)
    hour_files = {
        h: hour_cache / f"{_base_cache(h.date().isoformat(), f'{h.hour:02d}h', area, codes)}.parquet"
        for h in horas
    }
    sem_cache = [h for h in horas if rebaixar or not hour_files[h].exists()]
    arquivos_por_hora = {}
    if sem_cache:
        print(f"GOES-16 FDCF: {ini} a {fim}, BBOX {area}, Mask {codes}")
        print(f"Consultando catálogo NOAA ({len(dias)} dias)...")
        with requests.Session() as sess:
            for dia in dias:
                d = _listar_dia(dia, session=sess)
                for t, k in d.items():
                    hora = t.floor("h")
                    arquivos_por_hora.setdefault(hora, {})[t] = k
        print(f"{sum(map(len, arquivos_por_hora.values()))} cenas encontradas; "
              f"{len(horas) - len(sem_cache)}/{len(horas)} horas já em cache.")

    partes = []
    faltas = 0
    # One bounded executor for the whole interval (connection reuse, low RAM).
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for i, hora in enumerate(horas, 1):
            cache = hour_files[hora]
            if cache.exists() and not rebaixar:
                df_hora = pd.read_parquet(cache)
            else:
                cenas = dict(sorted(arquivos_por_hora.get(hora, {}).items()))
                if not cenas:
                    faltas += 1
                    continue  # missing NOAA product, not a zero-FRP scan
                print(f"\rProcessando {hora:%d/%m/%Y %H} UTC | "
                      f"{i}/{len(horas)} horas | {len(cenas)} cenas", end="", flush=True)
                df_hora = _processar_hora(cenas, area, codes, cache, pool)
            partes.append(df_hora)
    print()

    if not partes:
        raise RuntimeError("Nenhuma cena GOES-16 disponível no período solicitado.")
    tudo = pd.concat(partes, ignore_index=True)
    df_goes = tudo.loc[tudo["mask_code"].notna(), _COLUNAS].copy().reset_index(drop=True)
    if not df_goes.empty:
        df_goes["mask_code"] = df_goes["mask_code"].astype("int16")

    grouped = tudo.groupby("time_utc", sort=True)
    stats = grouped.agg(
        focos=("mask_code", "count"),
        frp_mean_mw=("frp_mw", "mean"),
        frp_max_mw=("frp_mw", "max"),
        frp_sum_mw=("frp_mw", "sum"),
        n_frp_validos=("frp_mw", "count"),
    )
    # No detections within a valid scan => mean is undefined, not 0 MW.
    stats.loc[stats["n_frp_validos"] == 0, "frp_sum_mw"] = np.nan
    times = pd.date_range(pd.Timestamp(ini, tz="UTC"),
                          pd.Timestamp(fim + timedelta(days=1), tz="UTC"),
                          freq=f"{TIME_BIN_MINUTES}min", inclusive="left")
    serie = stats.reindex(times)
    serie["cena_disponivel"] = serie["focos"].notna()
    serie["focos"] = serie["focos"].astype("Int64")
    serie["n_frp_validos"] = serie["n_frp_validos"].astype("Int64")
    serie.index.name = "time_utc"
    serie = serie.reset_index()

    # Do not lock incomplete downloads into the final cache; hour cache already exists.
    if not faltas:
        df_goes.to_parquet(fire_file, engine="pyarrow", compression="zstd", index=False)
        serie.to_parquet(series_file, engine="pyarrow", compression="zstd", index=False)
    else:
        print(f"Aviso: {faltas} horas sem produto no catálogo da NOAA. "
              "Cache horário preservado; tabelas agregadas não persistidas.")

    print(f"{serie['cena_disponivel'].sum()} cenas disponíveis / {len(serie)} slots de 10 min; "
          f"{len(df_goes)} pixels de fogo na BBOX.")
    return {
        "df_goes": df_goes,
        "serie_temporal": serie,
        "arquivo": fire_file if fire_file.exists() and not faltas else None,
        "arquivo_serie": series_file if series_file.exists() and not faltas else None,
        "bbox": area, "inicio": ini, "fim": fim,
    }


# EXPORT_TO_GITHUB
# ============================================================
# 5. ONLY ONE PLOT: MEAN FRP PER OBSERVATION (MW)
# ============================================================
def gerar_figura_frp_media(serie, inicio, fim, bbox, codigos, mostrar=True):
    fig, ax = plt.subplots(figsize=FIG_SIZE, layout="constrained")
    ts = pd.to_datetime(serie["time_utc"], utc=True).dt.tz_localize(None)
    valor = pd.to_numeric(serie["frp_mean_mw"], errors="coerce")
    ax.plot(ts, valor, color=COLORS["green"], lw=1.05, marker="o",
            markersize=1.35, markeredgewidth=0)
    ax.set_ylabel("Mean fire radiative power (MW)")
    ax.set_xlabel("Acquisition date and time (UTC)")
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    ax.set_xlim(pd.Timestamp(inicio), pd.Timestamp(fim) + pd.Timedelta(days=1))
    for dia in pd.date_range(pd.Timestamp(inicio) + pd.Timedelta(days=1),
                             pd.Timestamp(fim), freq="D"):
        ax.axvline(dia, color="#C9C9C9", lw=0.55, zorder=0)
    ax.grid(axis="y", color="#E2E2E2", linewidth=0.5)
    fig_path = FIG_DIR / (
        f"goes16_fdcf_mean_frp_{inicio}_{fim}_{_id_area(bbox)}_"
        f"mask{'-'.join(map(str,codigos))}"
    )
    if SAVE_FIGURES:
        FIG_DIR.mkdir(parents=True, exist_ok=True)
        for ext in ("pdf", "png"):
            path = fig_path.with_suffix(f".{ext}")
            if OVERWRITE_FIGURES or not path.exists():
                fig.savefig(path, facecolor="white", bbox_inches="tight", dpi=FIG_DPI)
                print(f"Figura salva: {path}")
    if mostrar:
        plt.show()
    else:
        plt.close(fig)
    return fig


# EXPORT_TO_GITHUB
# ============================================================
# 6. PUBLIC ENTRYPOINT — CALL FROM ANY COLAB AFTER GITHUB EXEC
# ============================================================
def baixar_goes(
    data_inicial=None, data_final=None, bbox=None, codigos_fogo=None,
    pasta=None, rebaixar=False, mostrar=True, max_downloads=None,
):
    """Generate exactly one figure and return pixel/temporal DataFrames.

    Example in another Colab: resultado = baixar_goes()
    resultado['df_goes'] : individual fire-pixel detections (one row per pixel/scan)
    resultado['serie_temporal'] : 10-minute statistics, including missing slots
    """
    print("PyroCb GOES-16:", PYROCB_VERSION)
    resultado = baixar_e_processar_goes(
        data_inicial=data_inicial, data_final=data_final, bbox=bbox,
        codigos_fogo=codigos_fogo, pasta=pasta,
        rebaixar=rebaixar, max_downloads=max_downloads,
    )
    codes = CODIGOS_FOGO if codigos_fogo is None else tuple(codigos_fogo)
    fig = gerar_figura_frp_media(
        resultado["serie_temporal"], resultado["inicio"], resultado["fim"],
        resultado["bbox"], codes, mostrar=mostrar,
    )
    resultado["fig_frp_media"] = fig
    return resultado
