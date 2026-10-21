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

