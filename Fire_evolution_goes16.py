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
BBOX = "-62.5,-21,-57,-17"      # west, south, east, north; same as MODIS
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

