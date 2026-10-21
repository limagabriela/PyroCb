# Gerado de Fire_evolution_pyrocb.ipynb. Edite o notebook-fonte no Colab.

# EXPORT_TO_GITHUB
"""Funções do projeto PyroCb para baixar, filtrar e plotar dados do FIRMS.

Este código é exportado automaticamente para Fire_evolution_pyrocb.py.
Não armazene a MAP_KEY aqui.
"""

from datetime import date, timedelta
from io import StringIO
from pathlib import Path
import subprocess
import sys

import matplotlib.pyplot as plt
import pandas as pd
import requests

FAMILIAS = ("MODIS", "VIIRS_SNPP", "VIIRS_NOAA20", "VIIRS_NOAA21")
BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api"


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
# FUNÇÃO PRINCIPAL USADA PELOS OUTROS COLABS
# ==================================================

def baixar_firms(
    map_key,
    data_inicial="2019-08-16",
    data_final="2019-08-19",
    bbox="-59,-21,-53,-17",
    confianca_min=50,
    pasta="/content/firms/dados",
    rebaixar=False,
    mostrar=True,
):
    """Fluxo completo: download, filtro, Parquet e figuras.

    Returns:
        dict com df_firms, fig_serie, fig_mapa, serie_temporal, arquivo e resumo.
    """
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
        inicio=resultado["inicio"],
        fim=resultado["fim"],
        mostrar=mostrar,
    )
    fig_mapa = gerar_figura_mapa(
        resultado["df_firms"],
        bbox=resultado["bbox"],
        mostrar=mostrar,
    )

    return {
        "df_firms": resultado["df_firms"],
        "fig_serie": fig_serie,
        "fig_mapa": fig_mapa,
        "serie_temporal": serie_temporal,
        "arquivo": resultado["arquivo"],
        "resumo": resultado["resumo"],
    }


# EXPORT_TO_GITHUB
# ==================================================
# PUBLICATION FIGURE SETTINGS — NATURE-LIKE
# ==================================================

import math
import matplotlib as mpl
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from pathlib import Path

# --------------------------------------------------
# FIGURE DIMENSIONS
# Nature: 89 mm (single column), 183 mm (double)
# --------------------------------------------------

MM = 1 / 25.4

FIG_WIDTH_MM = 183
FIG_SERIES_HEIGHT_MM = 75
FIG_MAP_HEIGHT_MM = 125

FIG_SERIES_SIZE = (
    FIG_WIDTH_MM * MM,
    FIG_SERIES_HEIGHT_MM * MM
)

FIG_MAP_SIZE = (
    FIG_WIDTH_MM * MM,
    FIG_MAP_HEIGHT_MM * MM
)

# --------------------------------------------------
# EXPORT
# --------------------------------------------------

FIG_DIR = Path("/content/firms/figures")

SAVE_FIGURES = True
OVERWRITE_FIGURES = False

FIG_DPI = 450

# --------------------------------------------------
# ACCESSIBLE COLOR PALETTE — WONG
# --------------------------------------------------

COLORS = {
    "blue": "#0072B2",
    "orange": "#E69F00",
    "vermillion": "#D55E00",
    "green": "#009E73",
    "purple": "#CC79A7",
    "black": "#222222",
    "gray": "#737373",
    "light_gray": "#D9D9D9",
    "white": "#FFFFFF",
}

COLOR_SERIES = COLORS["blue"]
COLOR_MODIS = COLORS["vermillion"]
COLOR_VIIRS = COLORS["blue"]

# --------------------------------------------------
# MATPLOTLIB STYLE
# --------------------------------------------------

mpl.rcParams.update({

    # Fonts
    "font.family": "sans-serif",
    "font.sans-serif": [
        "Arial",
        "Helvetica",
        "Liberation Sans",
        "DejaVu Sans"
    ],

    "font.size": 7,
    "axes.labelsize": 7,
    "xtick.labelsize": 6,
    "ytick.labelsize": 6,
    "legend.fontsize": 6.5,

    # Axes
    "axes.linewidth": 0.75,
    "axes.edgecolor": COLORS["black"],
    "axes.labelcolor": COLORS["black"],

    "axes.spines.top": False,
    "axes.spines.right": False,

    # Ticks
    "xtick.direction": "out",
    "ytick.direction": "out",

    "xtick.major.size": 3,
    "ytick.major.size": 3,

    "xtick.major.width": 0.65,
    "ytick.major.width": 0.65,

    "xtick.color": COLORS["black"],
    "ytick.color": COLORS["black"],

    # Lines
    "lines.linewidth": 1.0,
    "lines.markersize": 3,

    # Background
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",

    # No decorative grid
    "axes.grid": False,

    # Legend
    "legend.frameon": False,

    # PDF / SVG
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "svg.fonttype": "none",

    # Display
    "figure.dpi": 140,
    "savefig.dpi": FIG_DPI,
})


# --------------------------------------------------
# FIGURE EXPORT FUNCTION
# --------------------------------------------------

def save_publication_figure(fig, name):

    if not SAVE_FIGURES:
        return

    FIG_DIR.mkdir(parents=True, exist_ok=True)

    for ext in ("pdf", "png"):

        path = FIG_DIR / f"{name}.{ext}"

        if path.exists() and not OVERWRITE_FIGURES:
            print(f"Figure already exists: {path}")
            continue

        fig.savefig(
            path,
            dpi=FIG_DPI,
            facecolor="white"
        )

        print(f"Figure saved: {path}")

# EXPORT_TO_GITHUB
# ==================================================
# FIGURE 1 — TEMPORAL EVOLUTION OF FIRE DETECTIONS
# ==================================================

def gerar_figura_serie_temporal(
    df_firms,
    inicio=None,
    fim=None,
    mostrar=True
):

    # ----------------------------------------------
    # DAILY COUNTS
    # ----------------------------------------------

    dates = pd.to_datetime(
        df_firms["acq_date"],
        errors="coerce"
    ).dt.normalize()

    counts = dates.dropna().value_counts().sort_index()

    if counts.empty:
        raise ValueError("No valid acquisition dates.")

    start = pd.Timestamp(inicio) if inicio else counts.index.min()
    end = pd.Timestamp(fim) if fim else counts.index.max()

    days = pd.date_range(start, end, freq="D")

    serie_temporal = pd.DataFrame({
        "data": days,
        "focos": counts.reindex(days, fill_value=0).to_numpy()
    })

    # ----------------------------------------------
    # FIGURE
    # ----------------------------------------------

    fig, ax = plt.subplots(
        figsize=FIG_SERIES_SIZE,
        layout="constrained"
    )

    ax.plot(
        serie_temporal["data"],
        serie_temporal["focos"],
        color=COLOR_SERIES,
        linewidth=1.2,
        marker="o" if len(days) <= 20 else None,
        markersize=3.5,
        markerfacecolor=COLOR_SERIES,
        markeredgewidth=0,
    )

    # ----------------------------------------------
    # AXES
    # ----------------------------------------------

    ax.set_xlabel("Date (UTC)")
    ax.set_ylabel("Active-fire detections")

    ax.set_ylim(
        0,
        max(1, serie_temporal["focos"].max() * 1.10)
    )

    if len(days) > 1:
        ax.set_xlim(days[0], days[-1])

    else:
        ax.set_xlim(
            days[0] - pd.Timedelta(hours=12),
            days[0] + pd.Timedelta(hours=12)
        )

    # Date formatting
    locator = mdates.AutoDateLocator(
        minticks=3,
        maxticks=6
    )

    ax.xaxis.set_major_locator(locator)

    ax.xaxis.set_major_formatter(
        mdates.ConciseDateFormatter(locator)
    )

    # Integer counts
    ax.yaxis.set_major_locator(
        mticker.MaxNLocator(
            nbins=5,
            integer=True
        )
    )

    # ----------------------------------------------
    # EXPORT
    # ----------------------------------------------

    name = (
        f"firms_temporal_evolution_"
        f"{start:%Y%m%d}_{end:%Y%m%d}"
    )

    save_publication_figure(fig, name)

    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)

    return serie_temporal, fig

# EXPORT_TO_GITHUB
# ==================================================
# FIGURE 2 — SPATIAL DISTRIBUTION OF FIRE DETECTIONS
# ==================================================

def gerar_figura_mapa(
    df_firms,
    bbox="world",
    mostrar=True
):

    # ----------------------------------------------
    # DATA
    # ----------------------------------------------

    df = df_firms.copy()

    df["latitude"] = pd.to_numeric(
        df["latitude"], errors="coerce"
    )

    df["longitude"] = pd.to_numeric(
        df["longitude"], errors="coerce"
    )

    df = df.dropna(
        subset=["latitude", "longitude"]
    )

    if df.empty:
        raise ValueError("No valid coordinates.")

    viirs = df[df["sensor"] == "VIIRS"]
    modis = df[df["sensor"] == "MODIS"]

    # ----------------------------------------------
    # FIGURE
    # ----------------------------------------------

    fig, ax = plt.subplots(
        figsize=FIG_MAP_SIZE,
        layout="constrained"
    )

    # VIIRS — filled points
    if not viirs.empty:

        ax.scatter(
            viirs["longitude"],
            viirs["latitude"],
            s=7,
            color=COLOR_VIIRS,
            alpha=0.55,
            linewidths=0,
            label=f"VIIRS (n={len(viirs):,})",
            rasterized=len(viirs) > 3000,
            zorder=2
        )

    # MODIS — open circles
    if not modis.empty:

        ax.scatter(
            modis["longitude"],
            modis["latitude"],
            s=20,
            marker="o",
            facecolors="none",
            edgecolors=COLOR_MODIS,
            linewidths=0.7,
            alpha=0.85,
            label=f"MODIS (n={len(modis):,})",
            rasterized=len(modis) > 3000,
            zorder=3
        )

    # ----------------------------------------------
    # SPATIAL EXTENT
    # ----------------------------------------------

    if str(bbox).lower() != "world":

        west, south, east, north = map(
            float, bbox.split(",")
        )

        ax.set_xlim(west, east)
        ax.set_ylim(south, north)

        mean_lat = (south + north) / 2

    else:

        ax.set_xlim(-180, 180)
        ax.set_ylim(-90, 90)

        mean_lat = 0

    # Approximate geographic aspect ratio
    ax.set_aspect(
        1 / max(math.cos(math.radians(mean_lat)), 0.1),
        adjustable="box"
    )

    # ----------------------------------------------
    # AXES
    # ----------------------------------------------

    ax.set_xlabel("Longitude (°)")
    ax.set_ylabel("Latitude (°)")

    ax.xaxis.set_major_locator(
        mticker.MaxNLocator(nbins=5)
    )

    ax.yaxis.set_major_locator(
        mticker.MaxNLocator(nbins=5)
    )

    ax.xaxis.set_major_formatter(
        mticker.FormatStrFormatter("%.1f")
    )

    ax.yaxis.set_major_formatter(
        mticker.FormatStrFormatter("%.1f")
    )

    # Map frame
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.7)

    # ----------------------------------------------
    # LEGEND
    # ----------------------------------------------

    if ax.get_legend_handles_labels()[1]:

        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.01),
            ncol=2,
            frameon=False,
            handletextpad=0.4,
            columnspacing=1.5
        )

    # ----------------------------------------------
    # EXPORT
    # ----------------------------------------------

    dates = pd.to_datetime(
        df["acq_date"],
        errors="coerce"
    )

    start = dates.min()
    end = dates.max()

    area_id = (
        str(bbox)
        .replace(",", "_")
        .replace("-", "m")
        .replace(".", "p")
    )

    name = (
        f"firms_spatial_distribution_"
        f"{start:%Y%m%d}_{end:%Y%m%d}_{area_id}"
    )

    save_publication_figure(fig, name)

    if mostrar:
        from IPython.display import display
        display(fig)
        plt.close(fig)

    return fig

# EXPORT_TO_GITHUB
# ==================================================
# DOWNLOAD E PROCESSAMENTO DOS DADOS
# ==================================================

def baixar_e_processar_firms(
    map_key,
    data_inicial="2019-08-16",
    data_final="2019-08-19",
    bbox="-59,-21,-53,-17",
    confianca_min=50,
    pasta="/content/firms/dados",
    rebaixar=False,
):
    """Baixa MODIS + VIIRS, filtra por confiança e salva um Parquet.

    Returns:
        dict com df_firms, arquivo, resumo e datas.
    """
    if not str(map_key).strip():
        raise ValueError("Forneça map_key no notebook que chama baixar_e_processar_firms().")

    _garantir_pyarrow()

    inicio = date.fromisoformat(data_inicial)
    fim = date.fromisoformat(data_final)
    if inicio > fim:
        raise ValueError("data_inicial deve ser anterior ou igual a data_final")

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    area_id = bbox.lower().replace(",", "_").replace("-", "m").replace(".", "p")
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

