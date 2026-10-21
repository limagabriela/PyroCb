# Generated automatically from fire_evolution_pyrocb.ipynb. Edit the notebook, not this file.

# EXPORT_TO_GITHUB
"""PyroCb: NASA FIRMS MODIS-only temporal and interannual fire analysis.

Edit fire_evolution_pyrocb.ipynb, commit to GitHub; export to the Python module.
The calling Colab only needs the NASA FIRMS MAP_KEY.
"""

from calendar import monthrange, month_name
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
import math
import subprocess
import sys

import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import requests

PYROCB_VERSION = "2026-10-21-modis-frp-bt-anomalies-v4"
FAMILIAS = ("MODIS",)   # MODIS only, never VIIRS
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"


# EXPORT_TO_GITHUB
# ==================================================
# PARAMETERS — CHANGE ONLY HERE IN THE ORIGINAL NOTEBOOK
# ==================================================

DATA_INICIAL = "2019-08-16"
DATA_FINAL = "2019-08-19"
BBOX = "-62.5,-21,-57,-17"
CONFIANCA_MIN = 50

TIME_BIN_HOURS = 1

# Compare the same calendar month in reference year +/- 5 years.
MES_REFERENCIA = 8
ANO_REFERENCIA = 2019
ANOS_VIZINHOS = 5
GERAR_COMPARACAO_INTERANUAL = True
TEMP_SENSOR = "MODIS"  # use MODIS brightness band for all temperatures

PASTA_DADOS = "/content/firms/dados"
FIG_DIR = Path("/content/firms/figures")
MAP_DIR = Path("/content/firms/cache")

SAVE_FIGURES = True
OVERWRITE_FIGURES = True  # update image after changing style
FIG_DPI = 450

MM = 1 / 25.4
FIG_WIDTH_MM = 183
FIG_SERIES_HEIGHT_MM = 180
FIG_MAP_HEIGHT_MM = 125
FIG_ANNUAL_HEIGHT_MM = 93
FIG_SERIES_SIZE = (FIG_WIDTH_MM * MM, FIG_SERIES_HEIGHT_MM * MM)
FIG_MAP_SIZE = (FIG_WIDTH_MM * MM, FIG_MAP_HEIGHT_MM * MM)
FIG_ANNUAL_SIZE = (FIG_WIDTH_MM * MM, FIG_ANNUAL_HEIGHT_MM * MM)

COLORS = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "vermillion": "#D55E00",
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
    "legend.fontsize": 6.5,
    "axes.linewidth": 0.75,
    "axes.edgecolor": COLORS["black"],
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "xtick.major.width": 0.65,
    "ytick.major.width": 0.65,
    "axes.grid": False,
    "legend.frameon": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",
    "figure.dpi": 140,
    "savefig.dpi": FIG_DPI,
})


def save_publication_figure(fig, name):
    """Save vector PDF and high-resolution PNG."""
    if not SAVE_FIGURES:
        return
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("pdf", "png"):
        path = FIG_DIR / f"{name}.{ext}"
        if path.exists() and not OVERWRITE_FIGURES:
            print(f"Figure already exists: {path}")
            continue
        fig.savefig(path, dpi=FIG_DPI, facecolor="white", bbox_inches="tight")
        print(f"Figure saved: {path}")


def _id_area(bbox):
    """Stable file-name identifier from geographical bounds."""
    return (str(bbox).lower().strip().replace(" ", "")
            .replace(",", "_").replace("-", "m").replace(".", "p"))


# EXPORT_TO_GITHUB
# ==================================================
# FUNÇÕES AUXILIARES DA API
# ==================================================

class SemCoberturaFIRMS(RuntimeError):
    """The requested period has no complete MODIS availability."""


class SemDeteccoesFIRMS(RuntimeError):
    """No valid MODIS detections in the requested date/area period."""


def _consultar_disponibilidade(map_key):
    """Get available FIRMS sources once for an interannual batch."""
    resposta = requests.get(f"{BASE_URL}/data_availability/csv/{map_key}/ALL", timeout=120)
    resposta.raise_for_status()
    tab = pd.read_csv(StringIO(resposta.text))
    expected = {"data_id", "min_date", "max_date"}
    if not expected.issubset(tab.columns):
        raise RuntimeError("Unexpected FIRMS availability response")
    tab["data_id"] = tab["data_id"].astype(str).str.strip().str.upper()
    tab["min_date"] = pd.to_datetime(tab["min_date"]).dt.date
    tab["max_date"] = pd.to_datetime(tab["max_date"]).dt.date
    return tab.to_dict("records")


def _fonte_disponivel(linhas, familia, dia):
    """Prefere SP e usa NRT apenas nas datas ainda sem SP."""
    for modo in ("SP", "NRT"):
        nome = f"{familia}_{modo}"
        if any(
            item["data_id"] == nome and item["min_date"] <= dia <= item["max_date"]
            for item in linhas
        ):
            return nome
    return None


def _montar_tarefas(linhas, inicio, fim):
    """Monta requisições de até 5 dias para cada família instrumental."""
    tarefas = []
    cobertura = set()
    for familia in FAMILIAS:
        dia = inicio
        while dia <= fim:
            fonte = _fonte_disponivel(linhas, familia, dia)
            if fonte is None:
                dia += timedelta(days=1)
                continue
            primeiro = dia
            dias = 0
            while dia <= fim and dias < 5 and _fonte_disponivel(linhas, familia, dia) == fonte:
                cobertura.add(dia)
                dia += timedelta(days=1)
                dias += 1
            tarefas.append((fonte, primeiro, dias))
    return tarefas, cobertura


def _ler_csv_firms(resposta, fonte):
    """Interpreta a resposta da API FIRMS."""
    if resposta.status_code != 200:
        raise RuntimeError(f"Erro HTTP {resposta.status_code} consultando {fonte}.")

    conteudo = resposta.text.strip()
    if conteudo.lower().startswith(("no data", "no records")):
        return pd.DataFrame(columns=["latitude", "longitude", "confidence"])
    if not conteudo:
        raise RuntimeError(f"Resposta vazia da API para {fonte}.")

    df = pd.read_csv(StringIO(conteudo), dtype={"confidence": "string"})
    colunas_minimas = {"latitude", "longitude", "confidence"}
    if not colunas_minimas.issubset(df.columns):
        raise RuntimeError(f"Resposta inesperada da API ({fonte}): {conteudo[:200]}")
    return df


def _garantir_pyarrow():
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyarrow"])


# EXPORT_TO_GITHUB
# ==================================================
# MODIS-ONLY DOWNLOAD, FILTER AND PARQUET CACHE
# ==================================================

def baixar_e_processar_firms(
    map_key,
    data_inicial=None,
    data_final=None,
    bbox=None,
    confianca_min=None,
    pasta=None,
    rebaixar=False,
    disponibilidade=None,
):
    """Download MODIS SP (or NRT when SP is not available) in <=5-day blocks.

    Filters numeric MODIS confidence >= threshold after concatenating raw batches.
    Separate MODIS-only Parquet filenames prevent reuse of older MODIS+VIIRS caches.
    """
    if not str(map_key).strip():
        raise ValueError("A NASA FIRMS MAP_KEY must be supplied by the calling notebook.")
    data_inicial = DATA_INICIAL if data_inicial is None else str(data_inicial)
    data_final = DATA_FINAL if data_final is None else str(data_final)
    bbox = BBOX if bbox is None else bbox
    confianca_min = CONFIANCA_MIN if confianca_min is None else confianca_min
    pasta = PASTA_DADOS if pasta is None else pasta

    inicio, fim = date.fromisoformat(data_inicial), date.fromisoformat(data_final)
    if inicio > fim:
        raise ValueError("data_inicial must not be after data_final")

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    area_id = _id_area(bbox)
    arquivo = pasta / (
        f"FIRMS_MODIS_{data_inicial}_{data_final}_{area_id}_"
        f"confidence{confianca_min}.parquet"
    )

    if arquivo.exists() and not rebaixar:
        _garantir_pyarrow()
        df_firms = pd.read_parquet(arquivo)
        if (df_firms.empty or not {"sensor", "fonte_firms", "frp", "acq_date"}.issubset(df_firms.columns)
                or not df_firms["sensor"].eq("MODIS").all()):
            raise RuntimeError(f"Invalid MODIS-only cache: {arquivo}; use rebaixar=True")
        print(f"Reutilizando MODIS: {arquivo} ({len(df_firms):,} detecções)")
        return dict(df_firms=df_firms, arquivo=arquivo, resumo=pd.DataFrame(),
                    inicio=inicio, fim=fim, bbox=bbox)

    linhas = _consultar_disponibilidade(map_key) if disponibilidade is None else disponibilidade
    tarefas, cobertura = _montar_tarefas(linhas, inicio, fim)
    todos_dias = {inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)}
    faltantes = sorted(todos_dias - cobertura)
    if faltantes:
        raise SemCoberturaFIRMS(
            f"Missing MODIS availability for {len(faltantes)} days "
            f"between {inicio} and {fim}; first missing: {faltantes[0]}"
        )

    registros = []
    print(f"MODIS {inicio} – {fim}: {len(tarefas)} requisições (até 5 dias cada)")
    with requests.Session() as sess:
        for fonte, primeiro, dias in tarefas:
            url = f"{BASE_URL}/area/csv/{map_key}/{fonte}/{bbox}/{dias}/{primeiro.isoformat()}"
            df = _ler_csv_firms(sess.get(url, timeout=240), fonte)
            df["fonte_firms"] = fonte
            df["sensor"] = "MODIS"
            registros.append(df)

    df_bruto = pd.concat(registros, ignore_index=True)
    if df_bruto.empty:
        raise SemDeteccoesFIRMS(f"No MODIS fire detections: {inicio} – {fim}")

    conf_num = pd.to_numeric(
        df_bruto["confidence"].astype("string").str.strip().str.replace("%", "", regex=False),
        errors="coerce",
    )
    df_firms = df_bruto.loc[conf_num.ge(confianca_min).fillna(False)].copy().reset_index(drop=True)
    if df_firms.empty:
        raise SemDeteccoesFIRMS(
            f"No MODIS detections with confidence >= {confianca_min}: {inicio} – {fim}"
        )

    resumo = (
        df_bruto.groupby("fonte_firms").size().rename("recebidos").to_frame()
        .join(df_firms.groupby("fonte_firms").size().rename("mantidos"))
        .fillna(0).astype(int)
    )
    print(f"MODIS detecções: {len(df_bruto):,} brutas; {len(df_firms):,} filtradas")
    _garantir_pyarrow()
    df_firms.to_parquet(arquivo, engine="pyarrow", compression="zstd", index=False)
    print(f"Parquet salvo: {arquivo}")
    return dict(df_firms=df_firms, arquivo=arquivo, resumo=resumo,
                inicio=inicio, fim=fim, bbox=bbox)


# EXPORT_TO_GITHUB
# ==================================================
# FIGURE 1 — HOURLY FIRE EVOLUTION (THREE PANELS)
#   a. detections
#   b. mean + maximum FRP, side by side
#   c. mean + maximum brightness temperature, side by side
# ==================================================

def gerar_figura_serie_temporal(
    df_firms,
    inicio=None,
    fim=None,
    mostrar=True,
    bbox="world",
    confianca_min=50,
):
    df = df_firms.copy()
    dates = pd.to_datetime(df["acq_date"], errors="coerce")
    hhmm = (
        pd.to_numeric(df["acq_time"], errors="coerce")
        .astype("Int64").astype("string").str.zfill(4)
    )
    df["datetime_utc"] = pd.to_datetime(
        dates.dt.strftime("%Y-%m-%d") + " " + hhmm.str[:2] + ":" + hhmm.str[2:],
        format="%Y-%m-%d %H:%M", errors="coerce",
    )
    df = df.dropna(subset=["datetime_utc"])
    if df.empty:
        raise ValueError("No valid FIRMS acquisition timestamps.")

    df["frp_mw"] = pd.to_numeric(df["frp"], errors="coerce")
    df.loc[df["frp_mw"] < 0, "frp_mw"] = np.nan

    # MODIS thermal brightness (K), converted to degrees Celsius.
    temp_col = "brightness"
    if temp_col not in df:
        df[temp_col] = np.nan
    df["temp_c"] = np.nan
    mask = df["sensor"].eq("MODIS")
    temp_k = pd.to_numeric(df.loc[mask, temp_col], errors="coerce")
    df.loc[mask, "temp_c"] = temp_k.where(temp_k > 0) - 273.15

    df["bin_utc"] = df["datetime_utc"].dt.floor(f"{TIME_BIN_HOURS}h")
    start = pd.Timestamp(inicio).normalize() if inicio is not None else df["bin_utc"].min().normalize()
    end = pd.Timestamp(fim).normalize() if fim is not None else df["bin_utc"].max().normalize()
    if start > end:
        raise ValueError("Start date must precede end date")
    horas = pd.date_range(
        start,
        end + pd.Timedelta(days=1) - pd.Timedelta(hours=TIME_BIN_HOURS),
        freq=f"{TIME_BIN_HOURS}h",
    )
    serie = df.groupby("bin_utc").agg(
        focos=("sensor", "size"),
        frp_mean_mw=("frp_mw", "mean"),
        frp_max_mw=("frp_mw", "max"),
    ).reindex(horas)
    temperaturas = (
        df.loc[mask].groupby("bin_utc")["temp_c"]
        .agg(["mean", "max"]).reindex(horas)
    )
    serie["temp_mean_c"] = temperaturas["mean"]
    serie["temp_max_c"] = temperaturas["max"]
    serie["focos"] = serie["focos"].fillna(0).astype(int)
    serie.index.name = "data"
    serie_temporal = serie.reset_index()

    fig, axes = plt.subplots(3, 1, figsize=FIG_SERIES_SIZE, sharex=True,
                             layout="constrained")
    x = mdates.date2num(serie_temporal["data"])
    bin_width = TIME_BIN_HOURS / 24.0
    width = bin_width * 0.38
    azul, verde, laranja = COLORS["blue"], COLORS["green"], COLORS["vermillion"]

    axes[0].bar(x, serie_temporal["focos"], width=bin_width * 0.82,
                color=azul, linewidth=0)
    axes[0].set_ylabel("Fire detections")
    axes[0].set_ylim(bottom=0)

    axes[1].bar(x - width/2, serie_temporal["frp_mean_mw"], width=width,
                color=verde, label="Mean FRP", linewidth=0)
    axes[1].bar(x + width/2, serie_temporal["frp_max_mw"], width=width,
                color=laranja, label="Maximum FRP", linewidth=0)
    axes[1].set_ylabel("FRP (MW)")
    axes[1].set_ylim(bottom=0)
    axes[1].legend(loc="upper right", ncol=2, frameon=False)

    axes[2].bar(x - width/2, serie_temporal["temp_mean_c"], width=width,
                color=verde, label="Mean", linewidth=0)
    axes[2].bar(x + width/2, serie_temporal["temp_max_c"], width=width,
                color=laranja, label="Maximum", linewidth=0)
    axes[2].set_ylabel(f"Brightness temperature (°C)\n{TEMP_SENSOR}")
    axes[2].legend(loc="upper right", ncol=2, frameon=False)

    for ax, letter in zip(axes, "abc"):
        ax.text(-0.07, 1.01, letter, transform=ax.transAxes,
                fontsize=8, fontweight="bold", va="bottom")
        ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=4))
        ax.tick_params(direction="out", which="minor", bottom=False, left=False)
    axes[0].yaxis.set_major_locator(mticker.MaxNLocator(nbins=4, integer=True))

    locator = mdates.AutoDateLocator(minticks=3, maxticks=6)
    axes[-1].xaxis.set_major_locator(locator)
    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b\n%H:%M"))
    axes[-1].set_xlabel("Acquisition date and time (UTC)")
    axes[-1].set_xlim(
        mdates.date2num(horas[0]) - bin_width/2,
        mdates.date2num(horas[-1]) + bin_width/2,
    )

    name = (
        f"firms_temporal_3panels_{TIME_BIN_HOURS}h_"
        f"{start:%Y%m%d}_{end:%Y%m%d}_{_id_area(bbox)}_"
        f"modis_conf{confianca_min}"
    )
    save_publication_figure(fig, name)
    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)
    return serie_temporal, fig


# EXPORT_TO_GITHUB
# ==================================================
# FIGURE 2 — SPATIAL FIRE DENSITY (PLASMA) + COUNTRY BORDERS
# ==================================================

def _carregar_paises():
    """Natural Earth country polygons, cached locally (no credentials needed)."""
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise ImportError("Install geopandas in Colab: %pip install geopandas") from exc

    MAP_DIR.mkdir(parents=True, exist_ok=True)
    arquivo = MAP_DIR / "ne_110m_admin_0_countries.zip"
    if not arquivo.exists():
        url = "https://naturalearth.s3.amazonaws.com/110m_cultural/ne_110m_admin_0_countries.zip"
        resposta = requests.get(url, timeout=90)
        resposta.raise_for_status()
        arquivo.write_bytes(resposta.content)
    return gpd.read_file(f"zip://{arquivo}").to_crs("EPSG:4326")


def gerar_figura_mapa(
    df_firms,
    bbox="world",
    mostrar=True,
    inicio=None,
    fim=None,
    confianca_min=50,
    prefixo_arquivo="firms_spatial_density_plasma",
):
    df = df_firms.copy()
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])
    if df.empty:
        raise ValueError("No valid coordinates.")

    if str(bbox).lower() == "world":
        west, south, east, north = -180, -90, 180, 90
    else:
        west, south, east, north = map(float, bbox.split(","))
    mean_lat = (south + north) / 2
    padx = (east - west) * 0.02
    pady = (north - south) * 0.02
    xlim, ylim = (west-padx, east+padx), (south-pady, north+pady)
    world = _carregar_paises()
    world = world.cx[xlim[0]:xlim[1], ylim[0]:ylim[1]]

    fig, ax = plt.subplots(figsize=FIG_MAP_SIZE, layout="constrained")
    if not world.empty:
        world.plot(ax=ax, facecolor="#F3F1EE", edgecolor="none", zorder=0)

    # Number of detections per hexagon, not temperature or radiative power.
    span = max(east - west, north - south)
    gridsize = 120 if bbox == "world" else (45 if span <= 5 else 60 if span <= 15 else 80)
    hb = ax.hexbin(
        df["longitude"], df["latitude"], gridsize=gridsize,
        extent=(xlim[0], xlim[1], ylim[0], ylim[1]),
        mincnt=1, bins="log", cmap="plasma",
        linewidths=0, zorder=2,
    )
    if not world.empty:
        world.boundary.plot(ax=ax, color="#686868", linewidth=0.55, zorder=3)
    cbar = fig.colorbar(hb, ax=ax, pad=0.02, shrink=0.85)
    cbar.set_label("Fire detections per hexagon (log scale)")

    ax.set_xlim(*xlim)
    ax.set_ylim(*ylim)
    ax.set_aspect(1 / max(math.cos(math.radians(mean_lat)), 0.1), adjustable="box")
    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")
    ax.xaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.xaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f"))
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.1f"))
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.7)

    dates = pd.to_datetime(df["acq_date"], errors="coerce")
    start = pd.Timestamp(inicio) if inicio is not None else dates.min()
    end = pd.Timestamp(fim) if fim is not None else dates.max()
    name = (
        f"{prefixo_arquivo}_"
        f"{start:%Y%m%d}_{end:%Y%m%d}_{_id_area(bbox)}_"
        f"modis_conf{confianca_min}"
    )
    save_publication_figure(fig, name)
    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)
    return fig


# EXPORT_TO_GITHUB
# ==================================================
# INTERANNUAL FRP/BT FIGURES AND SPATIAL HEATMAP
# ==================================================

def gerar_figura_frp_interanual(serie_anual, mes, ano_referencia, bbox, confianca_min,
                                mostrar=True, anos_vizinhos=None):
    """One panel with mean and maximum MODIS FRP for the same month in each year."""
    fig, ax = plt.subplots(figsize=FIG_ANNUAL_SIZE, layout="constrained")
    x = np.arange(len(serie_anual))
    width = 0.36
    ax.bar(x - width/2, serie_anual["frp_mean_mw"], width=width,
           color=COLORS["green"], label="Mean FRP", linewidth=0)
    ax.bar(x + width/2, serie_anual["frp_max_mw"], width=width,
           color=COLORS["vermillion"], label="Maximum FRP", linewidth=0)
    ax.set_xticks(x, [str(y) for y in serie_anual["ano"]])
    ax.set_ylabel("FRP (MW)")
    ax.set_xlabel(f"Year ({month_name[mes]})")
    ax.set_ylim(bottom=0)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.legend(ncol=2, frameon=False, loc="upper right")
    name = (f"firms_modis_frp_interannual_month{mes:02d}_"
            f"year{ano_referencia}_pm{ANOS_VIZINHOS if anos_vizinhos is None else anos_vizinhos}_{_id_area(bbox)}_"
            f"conf{confianca_min}")
    save_publication_figure(fig, name)
    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)
    return fig



def gerar_figura_frp_interanual_separada(
    serie_anual, mes, ano_referencia, anos_vizinhos, bbox,
    confianca_min, metrica="mean", anomalia=False, mostrar=True,
):
    """Plot one monthly FRP statistic, absolute or relative to reference year.

    metrica: "mean", "max" or "sum" (sum of instantaneous FRP detections).
    Note that FRP sums across satellite overpasses do not represent fire energy.
    """
    opcoes = {
        "mean": ("frp_mean_mw", "Mean FRP", COLORS["green"]),
        "max": ("frp_max_mw", "Maximum FRP", COLORS["vermillion"]),
        "sum": ("frp_sum_mw", "Summed FRP", COLORS["blue"]),
    }
    if metrica not in opcoes:
        raise ValueError("metrica must be 'mean', 'max' or 'sum'")
    coluna_base, rotulo, cor = opcoes[metrica]
    coluna = coluna_base.replace("_mw", "_anomaly_mw") if anomalia else coluna_base

    anos = serie_anual["ano"].astype(int).to_numpy()
    valores = pd.to_numeric(serie_anual[coluna], errors="coerce").to_numpy(dtype=float)
    x = np.arange(len(anos))
    fig, ax = plt.subplots(figsize=FIG_ANNUAL_SIZE, layout="constrained")

    if anomalia:
        cores = [COLORS["gray"] if a == ano_referencia else
                 COLORS["green"] if np.isfinite(v) and v >= 0 else
                 COLORS["vermillion"] for a, v in zip(anos, valores)]
        ax.axhline(0, color=COLORS["black"], lw=0.9, zorder=1)
        ax.bar(x, valores, width=0.66, color=cores, linewidth=0, zorder=2)
        ax.set_ylabel(f"{rotulo} anomaly (MW)")
        ax.set_title(f"Relative to {ano_referencia}", fontsize=8, pad=7)
        validos = np.abs(valores[np.isfinite(valores)])
        if validos.size and validos.max() > 0:
            limite = validos.max() * 1.12
            ax.set_ylim(-limite, limite)
    else:
        ax.bar(x, valores, width=0.66, color=cor, linewidth=0, zorder=2)
        ax.set_ylabel(f"{rotulo} (MW)")
        ax.set_ylim(bottom=0)

    ax.set_xticks(x, [str(a) for a in anos])
    ax.set_xlabel(f"Year ({month_name[mes]})")
    ax.set_xlim(-0.6, len(anos) - 0.4)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.tick_params(axis="x", labelrotation=0)

    tipo = "anomaly" if anomalia else "absolute"
    name = (
        f"firms_modis_frp_{metrica}_{tipo}_month{mes:02d}_"
        f"reference{ano_referencia}_pm{anos_vizinhos}_"
        f"{_id_area(bbox)}_conf{confianca_min}"
    )
    save_publication_figure(fig, name)
    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)
    return fig


def gerar_figura_bt_interanual_separada(
    serie_anual, mes, ano_referencia, anos_vizinhos, bbox,
    confianca_min, metrica="mean", anomalia=False, mostrar=True,
):
    """Monthly MODIS brightness temperature (Celsius), or its reference-year anomaly.

    Brightness is a satellite pixel brightness temperature, not flame temperature.
    No monthly sum: temperatures are not additive across detections.
    """
    opcoes = {
        "mean": ("bt_mean_c", "Mean brightness temperature", COLORS["green"]),
        "max": ("bt_max_c", "Maximum brightness temperature", COLORS["vermillion"]),
    }
    if metrica not in opcoes:
        raise ValueError("metrica must be 'mean' or 'max'")
    coluna_base, rotulo, cor = opcoes[metrica]
    coluna = coluna_base.replace("_c", "_anomaly_c") if anomalia else coluna_base

    anos = serie_anual["ano"].astype(int).to_numpy()
    valores = pd.to_numeric(serie_anual[coluna], errors="coerce").to_numpy(dtype=float)
    x = np.arange(len(anos))
    fig, ax = plt.subplots(figsize=FIG_ANNUAL_SIZE, layout="constrained")

    if anomalia:
        cores = [COLORS["green"] if np.isfinite(v) and v >= 0 else
                 COLORS["vermillion"] for v in valores]
        ax.axhline(0, color=COLORS["black"], lw=0.9, zorder=1)
        ax.bar(x, valores, width=0.66, color=cores, linewidth=0, zorder=2)
        ax.set_ylabel(f"{rotulo} anomaly (°C)")
        ax.set_title(f"Relative to {ano_referencia}", fontsize=8, pad=7)
        validos = np.abs(valores[np.isfinite(valores)])
        if validos.size and validos.max() > 0:
            limite = validos.max() * 1.12
            ax.set_ylim(-limite, limite)
    else:
        ax.bar(x, valores, width=0.66, color=cor, linewidth=0, zorder=2)
        ax.set_ylabel(f"{rotulo} (°C)")
        # Do not clip at zero: MODIS brightness temperatures can be negative °C.

    ax.set_xticks(x, [str(a) for a in anos])
    ax.set_xlabel(f"Year ({month_name[mes]})")
    ax.set_xlim(-0.6, len(anos) - 0.4)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(nbins=5))
    ax.tick_params(axis="x", labelrotation=0)

    tipo = "anomaly" if anomalia else "absolute"
    name = (
        f"firms_modis_bt_{metrica}_{tipo}_month{mes:02d}_"
        f"reference{ano_referencia}_pm{anos_vizinhos}_"
        f"{_id_area(bbox)}_conf{confianca_min}"
    )
    save_publication_figure(fig, name)
    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)
    return fig


def comparar_mes_interanual(
    map_key,
    mes=None,
    ano_referencia=None,
    anos_vizinhos=None,
    bbox=None,
    confianca_min=None,
    pasta=None,
    rebaixar=False,
    mostrar=True,
):
    """Compare one complete calendar month across reference year +/- N years.

    All detections are MODIS only, same bbox and confidence threshold.
    Returns monthly FRP/BT statistics, their anomalies, and a pooled density map.
    """
    mes = MES_REFERENCIA if mes is None else int(mes)
    ano_referencia = ANO_REFERENCIA if ano_referencia is None else int(ano_referencia)
    anos_vizinhos = ANOS_VIZINHOS if anos_vizinhos is None else int(anos_vizinhos)
    bbox = BBOX if bbox is None else bbox
    confianca_min = CONFIANCA_MIN if confianca_min is None else confianca_min
    pasta = PASTA_DADOS if pasta is None else pasta
    if not 1 <= mes <= 12 or anos_vizinhos < 0:
        raise ValueError("mes must be 1..12 and anos_vizinhos must be >= 0")

    years = list(range(ano_referencia - anos_vizinhos,
                       ano_referencia + anos_vizinhos + 1))
    disponibilidade = None  # load only if needed for uncached intervals
    registros_anuais = []
    arquivos_anuais = {}
    dfs = []
    anos_sem_cobertura = []
    hoje = date.today()
    print(f"\n===== MODIS: {month_name[mes]} | {years[0]}–{years[-1]} | bbox {bbox} =====")

    for ano in years:
        inicio = date(ano, mes, 1)
        fim = date(ano, mes, monthrange(ano, mes)[1])
        if fim > hoje:
            print(f"{ano}: incomplete/future month; skipped")
            anos_sem_cobertura.append(ano)
            continue
        try:
            # First attempt to load the existing MODIS-only Parquet without contacting FIRMS.
            # If absent, the availability data are looked up once and reused.
            nome = Path(pasta) / (
                f"FIRMS_MODIS_{inicio.isoformat()}_{fim.isoformat()}_"
                f"{_id_area(bbox)}_confidence{confianca_min}.parquet"
            )
            if disponibilidade is None and not (nome.exists() and not rebaixar):
                disponibilidade = _consultar_disponibilidade(map_key)
            resultado = baixar_e_processar_firms(
                map_key=map_key, data_inicial=inicio.isoformat(),
                data_final=fim.isoformat(), bbox=bbox,
                confianca_min=confianca_min, pasta=pasta,
                rebaixar=rebaixar, disponibilidade=disponibilidade,
            )
        except SemCoberturaFIRMS as exc:
            print(f"{ano}: excluded (incomplete MODIS coverage): {exc}")
            anos_sem_cobertura.append(ano)
            continue
        except SemDeteccoesFIRMS:
            print(f"{ano}: zero qualifying MODIS detections")
            registros_anuais.append(dict(ano=ano, focos=0, frp_mean_mw=np.nan,
                                         frp_max_mw=np.nan, frp_sum_mw=np.nan,
                                         bt_mean_c=np.nan, bt_max_c=np.nan))
            continue

        df = resultado["df_firms"].copy()
        df["ano_comparacao"] = ano
        frp = pd.to_numeric(df["frp"], errors="coerce")
        frp = frp.where(frp >= 0)
        # Match the hourly figure: MODIS brightness in Kelvin converted to Celsius.
        bt_k = pd.to_numeric(df["brightness"], errors="coerce")
        bt_c = bt_k.where(bt_k > 0) - 273.15
        registros_anuais.append(dict(
            ano=ano, focos=len(df), frp_mean_mw=frp.mean(),
            frp_max_mw=frp.max(), frp_sum_mw=frp.sum(min_count=1),
            bt_mean_c=bt_c.mean(), bt_max_c=bt_c.max(),
        ))
        arquivos_anuais[ano] = resultado["arquivo"]
        dfs.append(df)
        print(f"{ano}: {len(df):,} detecções; mean FRP={frp.mean():.2f} MW; "
              f"max FRP={frp.max():.2f} MW")

    if not registros_anuais:
        raise RuntimeError("No complete year/month with MODIS coverage and valid data")
    serie_anual = pd.DataFrame(registros_anuais).sort_values("ano").reset_index(drop=True)

    # All anomaly values must use the SAME user-selected reference year.
    referencia = serie_anual.loc[serie_anual["ano"].eq(ano_referencia)]
    if referencia.empty or referencia[["frp_mean_mw", "frp_max_mw", "frp_sum_mw"]].isna().any().any():
        raise ValueError(
            f"Reference year {ano_referencia} has no valid MODIS FRP for "
            f"{month_name[mes]}; cannot compute year-minus-reference anomalies."
        )
    for metrica in ("mean", "max", "sum"):
        coluna = f"frp_{metrica}_mw"
        serie_anual[f"frp_{metrica}_anomaly_mw"] = (
            serie_anual[coluna] - float(referencia.iloc[0][coluna])
        )
    if referencia[["bt_mean_c", "bt_max_c"]].isna().any().any():
        raise ValueError(
            f"Reference year {ano_referencia} has no valid MODIS brightness for "
            f"{month_name[mes]}; cannot compute BT anomalies."
        )
    for metrica in ("mean", "max"):
        coluna = f"bt_{metrica}_c"
        serie_anual[f"bt_{metrica}_anomaly_c"] = (
            serie_anual[coluna] - float(referencia.iloc[0][coluna])
        )

    # Preserve the original grouped chart for compatibility.
    fig_frp_anual = gerar_figura_frp_interanual(
        serie_anual, mes, ano_referencia, bbox, confianca_min, mostrar=mostrar,
    )

    # New: one chart per statistic and per quantity (absolute / anomaly).
    figuras_frp = {}
    for metrica in ("mean", "max", "sum"):
        for tipo, anomalia in (("absoluto", False), ("anomalia", True)):
            figuras_frp[f"fig_frp_{metrica}_{tipo}"] = gerar_figura_frp_interanual_separada(
                serie_anual=serie_anual, mes=mes, ano_referencia=ano_referencia,
                anos_vizinhos=anos_vizinhos, bbox=bbox,
                confianca_min=confianca_min, metrica=metrica,
                anomalia=anomalia, mostrar=mostrar,
            )
    # BT plots: separately show mean/maximum, absolute and reference-year anomalies.
    figuras_bt = {}
    for metrica in ("mean", "max"):
        for tipo, anomalia in (("absoluto", False), ("anomalia", True)):
            figuras_bt[f"fig_bt_{metrica}_{tipo}"] = gerar_figura_bt_interanual_separada(
                serie_anual=serie_anual, mes=mes, ano_referencia=ano_referencia,
                anos_vizinhos=anos_vizinhos, bbox=bbox,
                confianca_min=confianca_min, metrica=metrica,
                anomalia=anomalia, mostrar=mostrar,
            )
    serie_anomalias = serie_anual[[
        "ano", "frp_mean_anomaly_mw", "frp_max_anomaly_mw", "frp_sum_anomaly_mw",
        "bt_mean_anomaly_c", "bt_max_anomaly_c",
    ]].copy()

    df_interanual = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    fig_mapa_interanual = None
    if not df_interanual.empty:
        fig_mapa_interanual = gerar_figura_mapa(
            df_interanual,
            bbox=bbox, mostrar=mostrar,
            inicio=date(int(serie_anual["ano"].min()), mes, 1),
            fim=date(int(serie_anual["ano"].max()), mes, monthrange(int(serie_anual["ano"].max()), mes)[1]),
            confianca_min=confianca_min,
            prefixo_arquivo=f"firms_modis_heatmap_month{mes:02d}_year{ano_referencia}_pm{anos_vizinhos}",
        )
    return dict(
        serie_anual=serie_anual,
        serie_anomalias=serie_anomalias,
        df_interanual=df_interanual,
        fig_frp_anual=fig_frp_anual,
        **figuras_frp,
        **figuras_bt,
        fig_mapa_interanual=fig_mapa_interanual,
        arquivos_anuais=arquivos_anuais,
        anos_sem_cobertura=anos_sem_cobertura,
    )


# EXPORT_TO_GITHUB
# ==================================================
# PUBLIC ENTRYPOINT — MAIN COLAB ONLY PASSES MAP_KEY
# ==================================================

def baixar_firms(
    map_key,
    data_inicial=None,
    data_final=None,
    bbox=None,
    confianca_min=None,
    pasta=None,
    rebaixar=False,
    mostrar=True,
):
    """Download/cache FIRMS and show the temporal and spatial figures.

    Date, region and plotting parameters live in the source notebook.
    Explicit optional arguments remain available for advanced usage.
    """
    data_inicial = DATA_INICIAL if data_inicial is None else data_inicial
    data_final = DATA_FINAL if data_final is None else data_final
    bbox = BBOX if bbox is None else bbox
    confianca_min = CONFIANCA_MIN if confianca_min is None else confianca_min
    pasta = PASTA_DADOS if pasta is None else pasta
    print(f"PyroCb module: {PYROCB_VERSION}")

    resultado = baixar_e_processar_firms(
        map_key=map_key,
        data_inicial=data_inicial,
        data_final=data_final,
        bbox=bbox,
        confianca_min=confianca_min,
        pasta=pasta,
        rebaixar=rebaixar,
    )
    serie_temporal, fig_serie = gerar_figura_serie_temporal(
        resultado["df_firms"],
        inicio=resultado["inicio"], fim=resultado["fim"],
        mostrar=mostrar, bbox=resultado["bbox"],
        confianca_min=confianca_min,
    )
    fig_mapa = gerar_figura_mapa(
        resultado["df_firms"],
        inicio=resultado["inicio"], fim=resultado["fim"],
        mostrar=mostrar, bbox=resultado["bbox"],
        confianca_min=confianca_min,
    )

    comparacao = (
        comparar_mes_interanual(
            map_key=map_key, mes=MES_REFERENCIA, ano_referencia=ANO_REFERENCIA,
            anos_vizinhos=ANOS_VIZINHOS, bbox=bbox,
            confianca_min=confianca_min, pasta=pasta,
            rebaixar=rebaixar, mostrar=mostrar,
        ) if GERAR_COMPARACAO_INTERANUAL else {}
    )
    return {
        "df_firms": resultado["df_firms"],
        "serie_temporal": serie_temporal,
        "fig_serie": fig_serie,
        "fig_mapa": fig_mapa,
        "arquivo": resultado["arquivo"],
        "resumo": resultado["resumo"],
        **comparacao,
    }

