# Generated automatically from fire_evolution_pyrocb.ipynb. Edit the notebook, not this file.

# EXPORT_TO_GITHUB
"""PyroCb: NASA FIRMS download and scientific figures.

Source of truth: fire_evolution_pyrocb.ipynb.
The main Colab only provides MAP_KEY; do not commit credentials.
"""

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

PYROCB_VERSION = "2026-10-21-hourly-3panels-hexbin-v1"
FAMILIAS = ("MODIS", "VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21")
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"


# EXPORT_TO_GITHUB
# ==================================================
# PARAMETERS — CHANGE ONLY HERE IN THE ORIGINAL NOTEBOOK
# ==================================================

DATA_INICIAL = "2019-08-16"
DATA_FINAL = "2019-08-19"
BBOX = "-59,-21,-53,-17"
CONFIANCA_MIN = 50

TIME_BIN_HOURS = 1
TEMP_SENSOR = "VIIRS"  # MODIS or VIIRS; do not pool thermal channels

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
FIG_SERIES_SIZE = (FIG_WIDTH_MM * MM, FIG_SERIES_HEIGHT_MM * MM)
FIG_MAP_SIZE = (FIG_WIDTH_MM * MM, FIG_MAP_HEIGHT_MM * MM)

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
# DOWNLOAD E PROCESSAMENTO DOS DADOS
# ==================================================

def baixar_e_processar_firms(
    map_key,
    data_inicial=None,
    data_final=None,
    bbox=None,
    confianca_min=None,
    pasta=None,
    rebaixar=False,
):
    """Baixa MODIS + VIIRS, filtra por confiança e salva um Parquet.

    Returns:
        dict com df_firms, arquivo, resumo e datas.
    """
    if not str(map_key).strip():
        raise ValueError("Forneça map_key no notebook que chama baixar_e_processar_firms().")

    data_inicial = DATA_INICIAL if data_inicial is None else data_inicial
    data_final = DATA_FINAL if data_final is None else data_final
    bbox = BBOX if bbox is None else bbox
    confianca_min = CONFIANCA_MIN if confianca_min is None else confianca_min
    pasta = PASTA_DADOS if pasta is None else pasta

    _garantir_pyarrow()

    inicio = date.fromisoformat(data_inicial)
    fim = date.fromisoformat(data_final)
    if inicio > fim:
        raise ValueError("data_inicial deve ser anterior ou igual a data_final")

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    area_id = _id_area(bbox)
    arquivo = pasta / (
        f"FIRMS_MODIS_VIIRS_{data_inicial}_{data_final}_{area_id}_"
        f"modis{confianca_min}_viirsNH.parquet"
    )

    if arquivo.exists() and not rebaixar:
        try:
            df_firms = pd.read_parquet(arquivo)
            if df_firms.empty or not {"sensor", "fonte_firms"}.issubset(df_firms.columns):
                raise ValueError("Parquet vazio ou sem colunas necessárias")
            resumo = pd.DataFrame()
            print(f"Reutilizando: {arquivo} ({len(df_firms):,} focos)")
            return {
                "df_firms": df_firms,
                "arquivo": arquivo,
                "resumo": resumo,
                "inicio": inicio,
                "fim": fim,
                "bbox": bbox,
            }
        except Exception as exc:
            raise RuntimeError(
                f"Parquet existente inválido: {arquivo}. Use rebaixar=True para refazer."
            ) from exc

    registros = []
    with requests.Session() as sess:
        disponibilidade = sess.get(f"{BASE_URL}/data_availability/csv/{map_key}/ALL", timeout=120)
        disponibilidade.raise_for_status()
        tab = pd.read_csv(StringIO(disponibilidade.text))

        colunas = {"data_id", "min_date", "max_date"}
        if not colunas.issubset(tab.columns):
            raise RuntimeError(f"Resposta inesperada da disponibilidade: {disponibilidade.text[:200]}")

        tab["data_id"] = tab["data_id"].astype(str).str.strip().str.upper()
        tab["min_date"] = pd.to_datetime(tab["min_date"]).dt.date
        tab["max_date"] = pd.to_datetime(tab["max_date"]).dt.date

        linhas = tab.to_dict("records")
        tarefas, cobertura = _montar_tarefas(linhas, inicio, fim)

        todos_dias = {inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)}
        faltantes = sorted(todos_dias - cobertura)
        if faltantes:
            raise RuntimeError(
                f"Sem cobertura MODIS/VIIRS para {len(faltantes)} dia(s), por exemplo {faltantes[:3]}."
            )

        print("Fontes consultadas:", ", ".join(dict.fromkeys(t[0] for t in tarefas)))
        print("A filtragem de confiança ocorre somente depois de juntar todas as fontes.\n")

        for fonte, primeiro, dias in tarefas:
            url = f"{BASE_URL}/area/csv/{map_key}/{fonte}/{bbox}/{dias}/{primeiro.isoformat()}"
            df = _ler_csv_firms(sess.get(url, timeout=240), fonte)
            df["fonte_firms"] = fonte
            df["sensor"] = "MODIS" if fonte.startswith("MODIS") else "VIIRS"
            registros.append(df)
            print(f"{fonte:20s} {primeiro.isoformat()} (+{dias-1}): {len(df):,} recebidos")

    df_bruto = pd.concat(registros, ignore_index=True)
    conf = df_bruto["confidence"].astype("string").str.strip().str.lower()
    conf_num = pd.to_numeric(conf.str.replace("%", "", regex=False), errors="coerce")

    filtro_modis = df_bruto["sensor"].eq("MODIS") & conf_num.ge(confianca_min)
    filtro_viirs = df_bruto["sensor"].eq("VIIRS") & conf.isin(["n", "nominal", "h", "high"])
    df_firms = df_bruto.loc[(filtro_modis | filtro_viirs).fillna(False)].copy().reset_index(drop=True)

    resumo = (
        df_bruto.groupby("fonte_firms").size().rename("recebidos").to_frame()
        .join(df_firms.groupby("fonte_firms").size().rename("mantidos"))
        .fillna(0).astype(int)
    )

    print("\n=========== RESULTADO DO FILTRO ===========")
    print(resumo.to_string())
    print(f"Total bruto: {len(df_bruto):,} | Total filtrado: {len(df_firms):,}")

    if df_firms.empty:
        raise RuntimeError("Nenhum foco passou no filtro; nenhum Parquet foi salvo.")

    df_firms.to_parquet(arquivo, engine="pyarrow", compression="zstd", index=False)
    print(f"\nParquet salvo em: {arquivo}")

    return {
        "df_firms": df_firms,
        "arquivo": arquivo,
        "resumo": resumo,
        "inicio": inicio,
        "fim": fim,
        "bbox": bbox,
    }


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

    # MODIS brightness and VIIRS I4 differ: compare within one sensor only.
    if TEMP_SENSOR not in ("MODIS", "VIIRS"):
        raise ValueError("TEMP_SENSOR must be MODIS or VIIRS")
    temp_col = "bright_ti4" if TEMP_SENSOR == "VIIRS" else "brightness"
    if temp_col not in df:
        df[temp_col] = np.nan
    df["temp_c"] = np.nan
    mask = df["sensor"].eq(TEMP_SENSOR)
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
        f"modis{confianca_min}_viirsNH_{TEMP_SENSOR.lower()}"
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
        f"firms_spatial_density_plasma_"
        f"{start:%Y%m%d}_{end:%Y%m%d}_{_id_area(bbox)}_"
        f"modis{confianca_min}_viirsNH"
    )
    save_publication_figure(fig, name)
    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)
    return fig


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
    return {
        "df_firms": resultado["df_firms"],
        "serie_temporal": serie_temporal,
        "fig_serie": fig_serie,
        "fig_mapa": fig_mapa,
        "arquivo": resultado["arquivo"],
        "resumo": resultado["resumo"],
    }

