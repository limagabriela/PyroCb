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

PYROCB_VERSION = "2026-10-21-modis-frp-anomalies-v3"
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

