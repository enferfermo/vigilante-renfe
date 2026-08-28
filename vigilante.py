#!/usr/bin/env python3
"""
Vigilante de plazas Renfe.

Pensado para correr como job puntual (GitHub Actions), NO como proceso vivo.
Cada ejecucion:
  1. consulta los trenes comprables para las fechas vigiladas
  2. los compara con los de la ejecucion anterior (state.json)
  3. avisa por Telegram solo de los que ANTES NO ESTABAN

Requiere renfe-bot clonado en ./renfe-bot
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# --- configuracion (viene por env desde el workflow) -------------------------

ORIGEN = os.environ["ORIGEN"]
DESTINO = os.environ["DESTINO"]
FECHAS = [f.strip() for f in os.environ["FECHAS"].split(",") if f.strip()]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

STATE_FILE = Path("state.json")
RENFE_BOT_DIR = Path("renfe-bot")


# --- scraping ----------------------------------------------------------------

# Captura filas de la tabla que imprime la CLI: hora salida, hora llegada, precio.
FILA = re.compile(r"(\d{2}:\d{2})\D+?(\d{2}:\d{2}).*?([\d,.]+)\s*€")


def buscar_trenes(fecha: str) -> dict[str, str]:
    """
    Devuelve {id_tren: descripcion} de los trenes COMPRABLES en esa fecha.

    Implementacion por subprocess sobre la CLI de renfe-bot. Funciona, pero es
    fragil: si cambian el formato de la tabla se rompe el parseo. Cuando tengas
    esto rodando, mirate renfe-bot/src/ e importa la funcion de busqueda
    directamente para saltarte el parseo de texto.
    """
    cmd = [
        sys.executable,
        "src/cli.py",
        "-o", ORIGEN,
        "-d", DESTINO,
        "--departure_date", fecha,
    ]
    env = {**os.environ, "PYTHONPATH": "./src"}

    try:
        salida = subprocess.run(
            cmd,
            cwd=RENFE_BOT_DIR,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        ).stdout
    except subprocess.TimeoutExpired:
        print(f"[!] timeout consultando {fecha}", file=sys.stderr)
        return {}

    trenes = {}
    for salida_h, llegada_h, precio in FILA.findall(salida):
        clave = f"{fecha} {salida_h}"
        trenes[clave] = f"{fecha} · {salida_h} → {llegada_h} · {precio} €"
    return trenes


# --- estado ------------------------------------------------------------------

def cargar_estado() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def guardar_estado(estado: dict[str, str]) -> None:
    STATE_FILE.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --- notificacion ------------------------------------------------------------

def avisar(mensaje: str) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    datos = urllib.parse.urlencode(
        {"chat_id": CHAT_ID, "text": mensaje, "disable_web_page_preview": "true"}
    ).encode()
    try:
        with urllib.request.urlopen(url, data=datos, timeout=20) as r:
            r.read()
    except Exception as e:
        print(f"[!] fallo enviando a Telegram: {e}", file=sys.stderr)


# --- main --------------------------------------------------------------------

def main() -> int:
    anterior = cargar_estado()
    actual: dict[str, str] = {}

    for fecha in FECHAS:
        actual.update(buscar_trenes(fecha))

    # Primera ejecucion: solo fotografiamos, no avisamos de todo el catalogo.
    if not anterior:
        guardar_estado(actual)
        print(f"Estado inicial guardado: {len(actual)} trenes disponibles.")
        return 0

    nuevos = [desc for clave, desc in actual.items() if clave not in anterior]

    if nuevos:
        cuerpo = "\n".join(f"• {d}" for d in sorted(nuevos))
        avisar(
            f"🚄 Plaza liberada {ORIGEN} → {DESTINO}\n\n{cuerpo}\n\n"
            f"https://www.renfe.com/es/es"
        )
        print(f"Avisado de {len(nuevos)} tren(es) nuevos.")
    else:
        print("Sin novedades.")

    guardar_estado(actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
