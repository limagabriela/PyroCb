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


