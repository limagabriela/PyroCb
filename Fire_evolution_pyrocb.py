# Generated from Fire_evolution_pyrocb.ipynb by GitHub Actions. Edit the notebook instead.

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

