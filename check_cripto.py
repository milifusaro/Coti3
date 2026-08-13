"""
Chequea si conviene: comprar dolares en Uala -> comprar cripto con esos
dolares (en cualquier exchange, al mejor precio) -> vender esa misma cripto
por pesos (en cualquier exchange, al mejor precio) -> quedarte con mas pesos
de los que gastaste comprando los dolares originales.

Escanea TODAS las criptomonedas que soporta CriptoYa (BTC, ETH, USDT, USDC,
DAI, SOL, XRP, etc.) y, para cada una, el mejor precio de compra en USD y el
mejor precio de venta en ARS entre TODOS los exchanges que la operan. Se
queda con la combinacion cripto+exchanges que mas ganancia deja.

Fuente de Uala: ComparaDolar.ar.
Fuente de criptos: CriptoYa (endpoint "cotizacion general", todos los
exchanges para un par coin/fiat).

Si hay ganancia (implicito > precio de Uala), manda un aviso a Telegram con
la cripto, los exchanges de compra/venta, y cuanto es la ganancia.

Variables de entorno necesarias:
  TELEGRAM_BOT_TOKEN  -> token del bot (te lo da @BotFather)
  TELEGRAM_CHAT_ID    -> tu chat id (te lo da @userinfobot)
  THRESHOLD_ARS       -> opcional, default 5.0 (ganancia minima en ARS por USD para avisar)
  VOLUMEN             -> opcional, default 100 (volumen de referencia para las cotizaciones)
  MONTO_ARS           -> opcional, default 100000 (monto de pesos de referencia para simular el circuito completo)
"""

import os
import re
import sys
import json
import time
import urllib.request
import urllib.error

UALA_URL = "https://comparadolar.ar/usd/uala"
CRIPTOYA_URL_TEMPLATE = "https://criptoya.com/api/{coin}/{fiat}/{volumen}"
TELEGRAM_API_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

# Todas las criptos que soporta el endpoint "cotizacion general" de CriptoYa Argentina.
COINS = [
    "BTC", "ETH", "USDT", "USDC", "DAI", "UXD", "USDP", "WLD", "BNB", "SOL",
    "XRP", "ADA", "AVAX", "DOGE", "TRX", "LINK", "DOT", "MATIC", "SHIB",
    "LTC", "BCH", "EOS", "XLM", "FTM", "AAVE", "UNI", "ALGO", "BAT", "PAXG",
    "CAKE", "AXS", "SLP", "MANA", "SAND", "CHZ",
]

# Exchanges que se excluyen de la comparacion (por ejemplo, porque no
# dejan retirar/transferir fondos con facilidad). Nombres tal como los
# devuelve la API de CriptoYa (en minuscula).
EXCHANGES_EXCLUIDOS = {"astropay"}

COMPRAS_PATTERN = re.compile(r"Compras a.{0,150}?([\d]{1,3}(?:\.\d{3})*,\d{2})", re.DOTALL)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}


def parse_ar_number(text: str) -> float:
    return float(text.replace(".", "").replace(",", "."))


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def fetch_json(url: str):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def strip_html_tags(html: str) -> str:
    sin_tags = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", sin_tags)


def solo_seccion_principal(texto_plano: str) -> str:
    corte = texto_plano.find("Otras opciones")
    return texto_plano if corte == -1 else texto_plano[:corte]


def get_uala_compra() -> float:
    """Precio al que Uala te vende dolares (lo que vos pagas al comprar)."""
    html = fetch_text(UALA_URL)
    seccion = solo_seccion_principal(strip_html_tags(html))
    match = COMPRAS_PATTERN.search(seccion)
    if not match:
        raise RuntimeError("No se pudo encontrar el precio de 'Compras a' de Uala en ComparaDolar.ar.")
    return parse_ar_number(match.group(1))


def mejor_exchange_mismo_lugar(coin: str, volumen: float):
    """Para una cripto, busca -entre los exchanges que la operan tanto en
    USD como en ARS- el que da la mejor tasa implicita comprando y
    vendiendo en el MISMO exchange (sin mover la cripto entre plataformas).
    Devuelve (exchange, precio_compra_usd, precio_venta_ars, implicito) o
    None si ningun exchange opera ambos pares para esa cripto."""
    url_usd = CRIPTOYA_URL_TEMPLATE.format(coin=coin, fiat="USD", volumen=volumen)
    url_ars = CRIPTOYA_URL_TEMPLATE.format(coin=coin, fiat="ARS", volumen=volumen)
    datos_usd = fetch_json(url_usd)
    datos_ars = fetch_json(url_ars)
    if not isinstance(datos_usd, dict) or not isinstance(datos_ars, dict):
        return None

    mejor = None
    for exch, info_usd in datos_usd.items():
        if exch.lower() in EXCHANGES_EXCLUIDOS:
            continue

        info_ars = datos_ars.get(exch)
        if not isinstance(info_usd, dict) or not isinstance(info_ars, dict):
            continue

        precio_usd = info_usd.get("totalAsk")
        precio_ars = info_ars.get("totalBid")
        if not isinstance(precio_usd, (int, float)) or precio_usd <= 0:
            continue
        if not isinstance(precio_ars, (int, float)) or precio_ars <= 0:
            continue

        implicito = precio_ars / precio_usd
        if mejor is None or implicito > mejor[3]:
            mejor = (exch, precio_usd, precio_ars, implicito)

    return mejor


def escanear_mejor_cripto(volumen: float):
    """Recorre todas las criptos y devuelve la combinacion (coin, exchange
    -el mismo para comprar y vender-, precio de compra, precio de venta,
    tasa implicita ARS/USD) que mas rinde."""
    mejor = None
    for coin in COINS:
        try:
            resultado = mejor_exchange_mismo_lugar(coin, volumen)
            time.sleep(0.15)
        except Exception as e:
            print(f"  aviso: no se pudo consultar {coin} ({e}), se omite")
            continue

        if resultado is None:
            continue

        exch, precio_usd, precio_ars, implicito = resultado
        if mejor is None or implicito > mejor["implicito"]:
            mejor = {
                "coin": coin,
                "exchange": exch,
                "precio_usd": precio_usd,
                "precio_ars": precio_ars,
                "implicito": implicito,
            }
    return mejor


def send_telegram_message(token: str, chat_id: str, text: str) -> None:
    url = TELEGRAM_API_TEMPLATE.format(token=token)
    payload = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        print(f"Error enviando mensaje a Telegram: {e.read().decode('utf-8')}", file=sys.stderr)
        raise


def main() -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    threshold_ars = float(os.environ.get("THRESHOLD_ARS", "5.0"))
    volumen = float(os.environ.get("VOLUMEN", "100"))
    monto_inicial = float(os.environ.get("MONTO_ARS", "100000"))

    if not bot_token or not chat_id:
        print("Faltan TELEGRAM_BOT_TOKEN y/o TELEGRAM_CHAT_ID en las variables de entorno.", file=sys.stderr)
        sys.exit(1)

    uala_compra = get_uala_compra()
    print(f"Uala (comprás a): {uala_compra:.2f} ARS/USD")

    mejor = escanear_mejor_cripto(volumen)
    if mejor is None:
        print("No se pudo obtener ninguna cotizacion valida de CriptoYa. Se aborta este chequeo.")
        return

    # Simulacion del circuito completo con un monto de referencia:
    # pesos -> dolares (Uala) -> cripto (compra) -> pesos (venta)
    dolares_comprados = monto_inicial / uala_compra
    cantidad_cripto = dolares_comprados / mejor["precio_usd"]
    monto_final = cantidad_cripto * mejor["precio_ars"]

    ganancia_ars = monto_final - monto_inicial
    ganancia_pct = (ganancia_ars / monto_inicial) * 100
    # Esto mismo, expresado "por dolar", para el umbral en ARS/USD:
    ganancia_por_usd = ganancia_ars / dolares_comprados

    print(f"Mejor cripto: {mejor['coin']}")
    print(f"  1) Comprás dolares en Uala: {monto_inicial:.2f} ARS -> {dolares_comprados:.4f} USD")
    print(f"  2) Comprás {mejor['coin']} en {mejor['exchange']}: {dolares_comprados:.4f} USD -> {cantidad_cripto:.6f} {mejor['coin']}")
    print(f"  3) Vendés {mejor['coin']} en {mejor['exchange']} (mismo exchange): {cantidad_cripto:.6f} {mejor['coin']} -> {monto_final:.2f} ARS")
    print(f"Resultado: {monto_inicial:.2f} ARS -> {monto_final:.2f} ARS (ganancia {ganancia_ars:.2f} ARS, {ganancia_pct:.2f}%)")

    if ganancia_por_usd >= threshold_ars:
        mensaje = (
            f"🟢 <b>Oportunidad detectada: pesos → dólares (Ualá) → cripto → pesos</b>\n\n"
            f"Empezás con: <b>{monto_inicial:,.0f} ARS</b>\n"
            f"1) Comprás dólares en Ualá: → {dolares_comprados:.2f} USD\n"
            f"2) Comprás y vendés <b>{mejor['coin']}</b> en <b>{mejor['exchange']}</b> (mismo exchange)\n"
            f"Terminás con: <b>{monto_final:,.0f} ARS</b>\n\n"
            f"Ganancia: <b>{ganancia_ars:,.0f} ARS ({ganancia_pct:.2f}%)</b>\n"
            f"(equivale a {ganancia_por_usd:.2f} ARS por dólar; umbral: {threshold_ars:.2f})"
        )
        send_telegram_message(bot_token, chat_id, mensaje)
        print("Notificacion enviada.")
    else:
        print("Diferencia por debajo del umbral, no se notifica.")


if __name__ == "__main__":
    main()
