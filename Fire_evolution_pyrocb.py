# Gerado de Fire_evolution_pyrocb.ipynb. Edite o notebook-fonte no Colab.

# EXPORT_TO_GITHUB
"""Download, filter and plot NASA FIRMS active fires (MODIS + VIIRS).

Public GitHub module: no API key is stored here.
Call baixar_firms(map_key=...) from a Colab notebook.
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


def _fonte(linhas, familia, dia):
    """Prefere SP e utiliza NRT somente nas datas ainda sem SP."""
    for modo in ("SP", "NRT"):
        nome = f"{familia}_{modo}"
        if any(
            item["data_id"] == nome
            and item["min_date"] <= dia <= item["max_date"]
            for item in linhas
        ):
            return nome
    return None


def _tarefas(linhas, inicio, fim):
    """Faz requisições de no máximo cinco dias por produto."""
    tarefas = []
    for familia in FAMILIAS:
        dia = inicio
        while dia <= fim:
            nome = _fonte(linhas, familia, dia)
            if nome is None:
                dia += timedelta(days=1)
                continue
            primeiro = dia
            dias = 0
            while dia <= fim and dias < 5 and _fonte(linhas, familia, dia) == nome:
                dia += timedelta(days=1)
                dias += 1
            tarefas.append((nome, primeiro, dias))
    return tarefas


def _ler_csv(resposta, fonte):
    if resposta.status_code != 200:
        raise RuntimeError(f"Erro HTTP {resposta.status_code} consultando {fonte}.")
    conteudo = resposta.text.strip()
    if conteudo.lower().startswith(("no data", "no records")):
        return pd.DataFrame(columns=["latitude", "longitude", "confidence"])
    if not conteudo:
        raise RuntimeError(f"Resposta vazia da API para {fonte}.")
    df = pd.read_csv(StringIO(conteudo), dtype={"confidence": "string"})
    if not {"latitude", "longitude", "confidence"}.issubset(df.columns):
        raise RuntimeError(f"Resposta inesperada da API ({fonte}): {conteudo[:160]}")
    return df


def _figuras(df, inicio, fim, bbox, mostrar):
    df_plot = df.copy()
    df_plot["acq_date"] = pd.to_datetime(df_plot["acq_date"], errors="coerce")
    df_plot["latitude"] = pd.to_numeric(df_plot["latitude"], errors="coerce")
    df_plot["longitude"] = pd.to_numeric(df_plot["longitude"], errors="coerce")

    serie_temporal = (
        df_plot.dropna(subset=["acq_date"])
        .groupby("acq_date")
        .size()
        .rename("focos")
        .reindex(pd.date_range(inicio, fim, freq="D"), fill_value=0)
        .rename_axis("data")
        .reset_index()
    )

    fig_serie, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot(serie_temporal["data"], serie_temporal["focos"], marker="o", lw=2)
    ax.set(title="FIRMS — Focos de calor por dia", xlabel="Data", ylabel="Número de detecções")
    ax.grid(alpha=0.25)
    fig_serie.autofmt_xdate()
    fig_serie.tight_layout()

    fig_mapa, ax = plt.subplots(figsize=(8.5, 7))
    for sensor, cor in (("MODIS", "#D97721"), ("VIIRS", "#C63B3B")):
        pts = df_plot.loc[df_plot["sensor"].eq(sensor)].dropna(
            subset=["longitude", "latitude"]
        )
        if not pts.empty:
            ax.scatter(pts["longitude"], pts["latitude"], s=10,
                       alpha=0.65, linewidths=0, color=cor, label=sensor,
                       rasterized=True)
    if bbox.lower() != "world":
        oeste, sul, leste, norte = (float(x) for x in bbox.split(","))
        ax.set_xlim(oeste, leste)
        ax.set_ylim(sul, norte)
    ax.set(title="FIRMS — Localização dos focos", xlabel="Longitude (°)", ylabel="Latitude (°)")
    ax.grid(alpha=0.25)
    if ax.get_legend_handles_labels()[1]:
        ax.legend(frameon=False)
    fig_mapa.tight_layout()

    if mostrar:
        from IPython.display import display
        display(fig_serie)
        display(fig_mapa)
        plt.close(fig_serie)
        plt.close(fig_mapa)

    return serie_temporal, fig_serie, fig_mapa


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
    """Baixa MODIS + VIIRS, filtra e retorna DataFrame, figuras e caminho Parquet.

    A chave da NASA FIRMS deve ser passada pelo Colab principal; jamais a
    inclua neste arquivo quando for publicar no GitHub.

    Returns:
        dict com df_firms, fig_serie, fig_mapa, serie_temporal e arquivo.
    """
    if not str(map_key).strip():
        raise ValueError("Forneça map_key no notebook que chama baixar_firms().")
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyarrow"])

    inicio, fim = date.fromisoformat(data_inicial), date.fromisoformat(data_final)
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
            print(f"Reutilizando: {arquivo} ({len(df_firms):,} focos)")
        except Exception as exc:
            raise RuntimeError(
                f"Parquet existente inválido: {arquivo}. Use rebaixar=True para refazer."
            ) from exc
    else:
        registros = []
        with requests.Session() as sess:
            r = sess.get(f"{BASE_URL}/data_availability/csv/{map_key}/ALL", timeout=120)
            disponibilidade = _ler_csv_disponibilidade(r)
            linhas = disponibilidade.to_dict("records")
            tarefas = _tarefas(linhas, inicio, fim)
            if not tarefas:
                raise RuntimeError("Nenhum produto MODIS/VIIRS disponível para essas datas.")

            # Cobertura deve existir para cada dia, considerando o conjunto dos sensores.
            cobertura = {
                primeiro + timedelta(days=i)
                for _, primeiro, dias in tarefas for i in range(dias)
            }
            todas = {inicio + timedelta(days=i) for i in range((fim - inicio).days + 1)}
            if cobertura != todas:
                faltantes = sorted(todas - cobertura)
                raise RuntimeError(f"Sem produtos FIRMS em {len(faltantes)} dia(s): {faltantes[:3]}")

            print("Fontes disponíveis:", ", ".join(dict.fromkeys(t[0] for t in tarefas)))
            for fonte, primeiro, dias in tarefas:
                url = (f"{BASE_URL}/area/csv/{map_key}/{fonte}/"
                       f"{bbox}/{dias}/{primeiro.isoformat()}")
                df = _ler_csv(sess.get(url, timeout=240), fonte)
                df["fonte_firms"] = fonte
                df["sensor"] = "MODIS" if fonte.startswith("MODIS") else "VIIRS"
                registros.append(df)
                print(f"{fonte:<20} {primeiro} (+{dias-1}): {len(df):,} registros")

        bruto = pd.concat(registros, ignore_index=True)
        conf = bruto["confidence"].astype("string").str.strip().str.lower()
        num = pd.to_numeric(conf.str.replace("%", "", regex=False), errors="coerce")
        filtro = (
            (bruto["sensor"].eq("MODIS") & num.ge(confianca_min))
            | (bruto["sensor"].eq("VIIRS") & conf.isin(["n", "nominal", "h", "high"]))
        )
        df_firms = bruto.loc[filtro.fillna(False)].reset_index(drop=True)
        resumo = (
            bruto.groupby("fonte_firms").size().rename("recebidos").to_frame()
            .join(df_firms.groupby("fonte_firms").size().rename("mantidos"))
            .fillna(0).astype(int)
        )
        print("\n" + resumo.to_string())
        print(f"Focos recebidos: {len(bruto):,} | mantidos: {len(df_firms):,}")
        if df_firms.empty:
            raise RuntimeError("Nenhum foco detectado após o filtro. Nenhum Parquet vazio será salvo.")

        df_firms.to_parquet(arquivo, engine="pyarrow", compression="zstd", index=False)
        print(f"Parquet salvo: {arquivo} ({arquivo.stat().st_size / 1024**2:.2f} MB)")

    if not {"acq_date", "latitude", "longitude"}.issubset(df_firms.columns):
        raise RuntimeError("Faltam colunas necessárias para gerar as figuras.")
    serie_temporal, fig_serie, fig_mapa = _figuras(
        df_firms, inicio, fim, bbox, mostrar
    )
    return {
        "df_firms": df_firms,
        "fig_serie": fig_serie,
        "fig_mapa": fig_mapa,
        "serie_temporal": serie_temporal,
        "arquivo": arquivo,
    }


def _ler_csv_disponibilidade(resposta):
    if resposta.status_code != 200:
        raise RuntimeError(f"Erro HTTP {resposta.status_code} consultando disponibilidade FIRMS.")
    df = pd.read_csv(StringIO(resposta.text))
    if not {"data_id", "min_date", "max_date"}.issubset(df.columns):
        raise RuntimeError(f"Resposta inesperada da disponibilidade FIRMS: {resposta.text[:160]}")
    df["data_id"] = df["data_id"].astype(str).str.strip().str.upper()
    df["min_date"] = pd.to_datetime(df["min_date"]).dt.date
    df["max_date"] = pd.to_datetime(df["max_date"]).dt.date
    return df

