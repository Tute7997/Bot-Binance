"""
Bot de trading MULTI-PAR para Kraken (dinero real, pero arranca en modo
simulacion). Version async de kraken-bot.py: en vez de recorrer los pares
secuencialmente, analiza BTC/USD, ETH/USD y SOL/USD en paralelo cada ciclo
(asyncio.gather + asyncio.to_thread sobre las mismas llamadas sincronas a la
API de Kraken/Supabase).

Diferencias clave respecto a kraken-bot.py (que sigue corriendo intacto,
como proceso separado, con sus propias tablas "sessions"/"account_state"/
"trades_kraken"/"kraken_heartbeat"):

- Cada par es su propio ciclo independiente: entra, sale, y vuelve a quedar
  disponible para una nueva entrada en el siguiente ciclo, sin esperar a los
  otros pares.
- Capital compuesto POR PAR: cada par arranca con un piso de
  config.CAPITAL_FLOOR_USD y va sumando la ganancia de sus propias sesiones
  cerradas (ver supabase_manager.obtener_capital_por_par), nunca por debajo
  del piso.
- Filtro de volatilidad nuevo (ATR%) ademas de la señal RSI+MACD, para no
  entrar en mercados planos.
- Salida por timeout: si pasan config.TIMEOUT_HORAS sin tocar TP ni SL, se
  cierra a mercado igual (motivo "TIMEOUT_3H").
- Sin JSON local de posiciones: el estado de posiciones abiertas se
  reconstruye al arrancar leyendo "trades_multi"/"sessions_multi" en
  Supabase (supabase_manager.obtener_posiciones_abiertas), que es la unica
  fuente de verdad.
- Toggle de simulacion propio (KRAKEN_MULTI_MODO_SIMULACION en .env.local,
  DISTINTO de KRAKEN_MODO_SIMULACION): los dos bots comparten el mismo
  archivo .env.local, asi que no pueden compartir la misma variable de
  simulacion sin que tocar una afecte a la otra.

IMPORTANTE (dinero real): este bot comparte la MISMA cuenta de Kraken que
kraken-bot.py. Antes de pasar KRAKEN_MULTI_MODO_SIMULACION a false, conviene
generar una segunda API key de Kraken exclusiva para este bot (evita
colisiones de nonce y deja un rastro de auditoria separado en Kraken).
"""

import asyncio
import base64
import csv
import hashlib
import hmac
import logging
import os
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd
import requests
from dotenv import load_dotenv

import config
import supabase_manager

# =============================================================================
# LOGGING
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.ARCHIVO_LOG_BOT, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("kraken_bot_multi")


# =============================================================================
# CARGA DE CONFIGURACION / CREDENCIALES
# =============================================================================

def cargar_config_kraken():
    """Obtiene las credenciales de Kraken desde .env.local (mismas que kraken-bot.py)."""
    load_dotenv(dotenv_path=".env.local")

    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_PRIVATE_KEY")

    if not api_key or not api_secret:
        raise ValueError("Faltan KRAKEN_API_KEY o KRAKEN_PRIVATE_KEY en .env.local")

    logger.info("Credenciales de Kraken cargadas correctamente.")
    return api_key, api_secret


def cargar_config_telegram():
    """Obtiene el token del bot y el chat id de Telegram desde .env.local."""
    load_dotenv(dotenv_path=".env.local")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        logger.warning(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env.local. "
            "No se podran enviar alertas."
        )

    return token, chat_id


TELEGRAM_TOKEN, TELEGRAM_CHAT_ID = cargar_config_telegram()


# =============================================================================
# ALERTAS POR TELEGRAM
# =============================================================================

def formatear_par(simbolo: str) -> str:
    """Convierte 'XXBTZUSD' en 'BTC/USD' para que se lea mejor en los mensajes."""
    return config.NOMBRES_PARES.get(simbolo, simbolo)


def formatear_duracion(minutos: int) -> str:
    """Convierte una duracion en minutos a un texto tipo '2 minutos' o '1h 15m'."""
    if minutos < 60:
        return f"{minutos} minutos"
    horas, minutos_restantes = divmod(minutos, 60)
    return f"{horas}h {minutos_restantes}m"


def enviar_telegram(mensaje: str) -> None:
    """Envia un mensaje de alerta al chat de Telegram configurado."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}

    try:
        respuesta = requests.post(url, data=payload, timeout=10)
        respuesta.raise_for_status()
    except requests.RequestException as error:
        logger.error(f"Error enviando mensaje a Telegram: {error}")


def enviar_telegram_entrada(par: str, precio_entrada: float, cantidad: float, capital_usado: float) -> None:
    target = precio_entrada * (1 + config.PROFIT_TARGET)
    stop = precio_entrada * (1 - config.STOP_LOSS)
    hora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prefijo = "🧪 [SIMULACIÓN] " if config.MODO_SIMULACION else ""

    enviar_telegram(
        f"{prefijo}🟢 OPERACIÓN ABIERTA (Kraken Multi)\n"
        f"Par: {formatear_par(par)}\n"
        f"Capital usado: ${capital_usado:,.2f} {config.MONEDA_CAPITAL}\n"
        f"Entrada: ${precio_entrada:,.2f} {config.MONEDA_CAPITAL}\n"
        f"Cantidad: {cantidad}\n"
        f"Target: ${target:,.2f} (+{config.PROFIT_TARGET * 100:.0f}%)\n"
        f"Stop Loss: ${stop:,.2f} (-{config.STOP_LOSS * 100:.0f}%)\n"
        f"Timeout: {config.TIMEOUT_HORAS}h\n"
        f"Hora: {hora}"
    )


def enviar_telegram_salida(
    par: str,
    precio_entrada: float,
    precio_salida: float,
    ganancia: float,
    ganancia_pct: float,
    motivo: str,
    duracion_minutos: int,
) -> None:
    textos_motivo = {
        "TP": "Take Profit",
        "SL": "Stop Loss",
        "TIMEOUT_3H": f"Timeout {config.TIMEOUT_HORAS}h (sin movimiento)",
    }
    texto_motivo = textos_motivo.get(motivo, motivo)
    signo = "+" if ganancia >= 0 else "-"
    prefijo = "🧪 [SIMULACIÓN] " if config.MODO_SIMULACION else ""

    enviar_telegram(
        f"{prefijo}✅ OPERACIÓN CERRADA (Kraken Multi)\n"
        f"Par: {formatear_par(par)}\n"
        f"Entrada: ${precio_entrada:,.2f} {config.MONEDA_CAPITAL}\n"
        f"Salida: ${precio_salida:,.2f} {config.MONEDA_CAPITAL}\n"
        f"Ganancia: {signo}${abs(ganancia):.2f} {config.MONEDA_CAPITAL} ({ganancia_pct:+.2f}%)\n"
        f"Motivo: {texto_motivo}\n"
        f"Duración: {formatear_duracion(duracion_minutos)}"
    )


# =============================================================================
# NONCE MONOTONICO Y THREAD-SAFE
# =============================================================================
# A diferencia de kraken-bot.py (secuencial, un nonce por vez), este bot
# dispara hasta 3 llamadas privadas concurrentes por ciclo (una por par,
# via asyncio.to_thread), asi que un timestamp en milisegundos puro puede
# repetirse o llegar desordenado. Este contador garantiza que cada nonce sea
# estrictamente mayor al anterior, sin importar el orden de llegada de los
# hilos.

_nonce_lock = threading.Lock()
_ultimo_nonce = 0


def generar_nonce() -> str:
    global _ultimo_nonce
    with _nonce_lock:
        nuevo = max(int(time.time() * 1000), _ultimo_nonce + 1)
        _ultimo_nonce = nuevo
        return str(nuevo)


# =============================================================================
# CLIENTE FIRMADO DE KRAKEN (HMAC-SHA512 + nonce)
# =============================================================================

def firmar_kraken(urlpath: str, data: dict, secret: str) -> str:
    """
    Firma segun el esquema oficial de Kraken:
    HMAC-SHA512(urlpath + SHA256(nonce + POST data), secret en base64) -> base64.
    """
    datos_codificados = (str(data["nonce"]) + urllib.parse.urlencode(data)).encode("utf-8")
    mensaje = urlpath.encode("utf-8") + hashlib.sha256(datos_codificados).digest()
    firma = hmac.new(base64.b64decode(secret), mensaje, hashlib.sha512)
    return base64.b64encode(firma.digest()).decode("utf-8")


def peticion_privada_kraken(path: str, api_key: str, api_secret: str, data: dict = None) -> dict:
    """Hace una peticion privada (POST, firmada) a la API de Kraken."""
    data = dict(data or {})
    data["nonce"] = generar_nonce()
    firma = firmar_kraken(path, data, api_secret)
    headers = {"API-Key": api_key, "API-Sign": firma}

    if config.DEBUG_AUTH:
        api_key_enmascarada = f"{api_key[:8]}..." if api_key else None
        logger.info(f"[KRAKEN_DEBUG_AUTH] POST {path} | API-Key={api_key_enmascarada} | nonce={data['nonce']}")

    respuesta = requests.post(f"{config.KRAKEN_BASE_URL}{path}", headers=headers, data=data, timeout=10)

    if config.DEBUG_AUTH:
        logger.info(f"[KRAKEN_DEBUG_AUTH] Respuesta {respuesta.status_code}: {respuesta.text[:300]}")

    respuesta.raise_for_status()
    datos = respuesta.json()

    if datos.get("error"):
        raise RuntimeError(f"Kraken devolvio un error: {datos['error']}")

    return datos.get("result", {})


def obtener_balance_kraken(api_key: str, api_secret: str, activo: str = config.ACTIVO_KRAKEN) -> float:
    """Obtiene el saldo de un activo en la cuenta de Kraken."""
    try:
        resultado = peticion_privada_kraken("/0/private/Balance", api_key, api_secret)
        return float(resultado.get(activo, 0))
    except (requests.RequestException, RuntimeError) as error:
        logger.error(f"Error obteniendo balance de {activo} en Kraken: {error}")
        return 0.0


def obtener_precio_kraken(par: str) -> float:
    """Obtiene el ultimo precio operado de un par (endpoint publico)."""
    respuesta = requests.get(f"{config.KRAKEN_BASE_URL}/0/public/Ticker", params={"pair": par}, timeout=10)
    respuesta.raise_for_status()
    datos = respuesta.json()

    if datos.get("error"):
        raise RuntimeError(f"Kraken devolvio un error: {datos['error']}")

    resultado = datos["result"]
    clave_par = next(iter(resultado))
    return float(resultado[clave_par]["c"][0])


def obtener_klines_kraken(par: str) -> pd.DataFrame:
    """
    Obtiene velas historicas reales de Kraken y arma un DataFrame con
    columnas OHLC + cierre. Se descarta la ultima vela porque todavia esta
    en formacion.
    """
    respuesta = requests.get(
        f"{config.KRAKEN_BASE_URL}/0/public/OHLC",
        params={"pair": par, "interval": config.INTERVALO_VELA_MINUTOS},
        timeout=10,
    )
    respuesta.raise_for_status()
    datos = respuesta.json()

    if datos.get("error"):
        raise RuntimeError(f"Kraken devolvio un error: {datos['error']}")

    resultado = datos["result"]
    clave_par = next(k for k in resultado if k != "last")
    velas = resultado[clave_par]

    columnas = ["tiempo", "apertura", "maximo", "minimo", "cierre", "vwap", "volumen", "cantidad_trades"]
    df = pd.DataFrame(velas, columns=columnas)

    for col in ["apertura", "maximo", "minimo", "cierre", "volumen"]:
        df[col] = df[col].astype(float)

    df["tiempo"] = pd.to_datetime(df["tiempo"], unit="s")
    df = df.iloc[:-1].reset_index(drop=True)

    logger.info(f"[{par}] Se obtuvieron {len(df)} velas cerradas de {config.INTERVALO_VELA_MINUTOS}m.")
    return df


def obtener_precision_kraken(par: str) -> tuple:
    """Obtiene la cantidad de decimales permitida y el volumen minimo para un par."""
    respuesta = requests.get(f"{config.KRAKEN_BASE_URL}/0/public/AssetPairs", params={"pair": par}, timeout=10)
    respuesta.raise_for_status()
    datos = respuesta.json()

    if datos.get("error"):
        raise RuntimeError(f"Kraken devolvio un error: {datos['error']}")

    resultado = datos["result"]
    clave_par = next(iter(resultado))
    info = resultado[clave_par]
    return int(info.get("lot_decimals", 8)), float(info.get("ordermin", 0))


def calcular_cantidad_compra_kraken(par: str, monto_usd: float, precio_actual: float) -> float:
    """Calcula la cantidad del activo base a comprar, redondeada a la precision de Kraken."""
    decimales, cantidad_minima = obtener_precision_kraken(par)
    cantidad = round(monto_usd / precio_actual, decimales)

    if cantidad < cantidad_minima:
        logger.warning(
            f"[{par}] Cantidad calculada ({cantidad}) es menor a la minima "
            f"permitida ({cantidad_minima}). No se puede operar con este monto."
        )
        return 0.0

    return cantidad


def crear_orden_kraken(api_key: str, api_secret: str, par: str, tipo: str, cantidad: float):
    """
    Crea una orden de mercado en Kraken (type: buy/sell). Si
    config.MODO_SIMULACION esta activo, NO manda nada real: solo loguea y
    avisa por Telegram que orden se habria hecho.
    """
    accion = "comprado" if tipo == "buy" else "vendido"

    if config.MODO_SIMULACION:
        logger.info(f"[SIMULACION] Se habria {accion} {cantidad} de {par} (orden market).")
        enviar_telegram(f"🧪 SIMULACIÓN: se habría {accion} {cantidad} de {formatear_par(par)}")
        return {"txid": None, "simulado": True}

    data = {"pair": par, "type": tipo, "ordertype": "market", "volume": str(cantidad)}
    try:
        resultado = peticion_privada_kraken("/0/private/AddOrder", api_key, api_secret, data)
        logger.info(f"[{par}] Orden {tipo} ejecutada en Kraken: {resultado.get('txid')}")
        return resultado
    except (requests.RequestException, RuntimeError) as error:
        logger.error(f"[{par}] Error creando orden {tipo} en Kraken: {error}")
        enviar_telegram(f"⚠️ Error ejecutando orden {tipo} en Kraken ({formatear_par(par)}): {error}")
        return None


# =============================================================================
# INDICADORES: RSI, MACD (identico a kraken-bot.py / Bot Binance) + ATR (nuevo)
# =============================================================================

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula el RSI (suavizado de Wilder, periodo 14) y el MACD
    (EMA 12/26 + linea de senal EMA 9) sobre la columna de precios de cierre.
    """
    df = df.copy()
    cierre = df["cierre"]

    delta = cierre.diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)

    media_ganancia = ganancia.ewm(alpha=1 / config.RSI_PERIODO, min_periods=config.RSI_PERIODO, adjust=False).mean()
    media_perdida = perdida.ewm(alpha=1 / config.RSI_PERIODO, min_periods=config.RSI_PERIODO, adjust=False).mean()

    fuerza_relativa = media_ganancia / media_perdida
    df["rsi"] = 100 - (100 / (1 + fuerza_relativa))
    df["rsi"] = df["rsi"].where(media_perdida != 0, 100)

    ema_rapida = cierre.ewm(span=config.MACD_RAPIDA, adjust=False).mean()
    ema_lenta = cierre.ewm(span=config.MACD_LENTA, adjust=False).mean()
    df["macd"] = ema_rapida - ema_lenta
    df["macd_senal"] = df["macd"].ewm(span=config.MACD_SENAL, adjust=False).mean()
    df["macd_histograma"] = df["macd"] - df["macd_senal"]

    return df


def calcular_atr(df: pd.DataFrame, periodo: int = config.ATR_PERIODO) -> pd.Series:
    """True Range (maximo de 3 formas) suavizado con media movil simple."""
    alto = df["maximo"]
    bajo = df["minimo"]
    cierre_previo = df["cierre"].shift(1)

    rango_verdadero = pd.concat(
        [alto - bajo, (alto - cierre_previo).abs(), (bajo - cierre_previo).abs()],
        axis=1,
    ).max(axis=1)

    return rango_verdadero.rolling(window=periodo).mean()


def senal_rsi_macd(df: pd.DataFrame) -> bool:
    """RSI < RSI_UMBRAL Y el MACD cruzo al alza en la ultima vela cerrada."""
    if len(df) < 2:
        return False

    vela_actual = df.iloc[-1]
    vela_anterior = df.iloc[-2]

    if pd.isna(vela_actual["rsi"]) or pd.isna(vela_actual["macd"]):
        return False

    rsi_en_sobreventa = vela_actual["rsi"] < config.RSI_UMBRAL
    cruce_alcista_macd = (
        vela_anterior["macd"] <= vela_anterior["macd_senal"]
        and vela_actual["macd"] > vela_actual["macd_senal"]
    )

    return rsi_en_sobreventa and cruce_alcista_macd


def filtro_volatilidad(df: pd.DataFrame, par: str) -> bool:
    """Exige que el ATR% (ATR / precio de cierre) supere un umbral minimo."""
    ultima = df.iloc[-1]

    if pd.isna(ultima["atr"]) or ultima["cierre"] == 0:
        return False

    atr_pct = ultima["atr"] / ultima["cierre"]
    logger.info(f"[{par}] ATR%={atr_pct:.4f} (umbral={config.UMBRAL_VOLATILIDAD_PCT})")
    return atr_pct >= config.UMBRAL_VOLATILIDAD_PCT


def senal_entrada(df: pd.DataFrame, par: str) -> bool:
    """Señal de compra: RSI+MACD de siempre, MAS el nuevo filtro de volatilidad."""
    return senal_rsi_macd(df) and filtro_volatilidad(df, par)


def verificar_salida(posicion: dict, precio_actual: float, ahora: datetime) -> str | None:
    """Devuelve "TP", "SL", "TIMEOUT_3H" o None, en ese orden de prioridad."""
    precio_entrada = posicion["precio_entrada"]
    variacion = (precio_actual - precio_entrada) / precio_entrada

    if variacion >= config.PROFIT_TARGET:
        return "TP"
    if variacion <= -config.STOP_LOSS:
        return "SL"

    segundos_abierto = (ahora - posicion["timestamp_entrada"]).total_seconds()
    if segundos_abierto >= config.TIMEOUT_SEGUNDOS:
        return "TIMEOUT_3H"

    return None


# =============================================================================
# REGISTRO LOCAL DE OPERACIONES (CSV, igual convencion que kraken-bot.py)
# =============================================================================

def registrar_operacion(par: str, tipo: str, precio: float, cantidad: float, motivo: str) -> None:
    existe_archivo = os.path.exists(config.ARCHIVO_LOG_OPERACIONES)

    try:
        with open(config.ARCHIVO_LOG_OPERACIONES, "a", newline="", encoding="utf-8") as archivo:
            escritor = csv.writer(archivo)
            if not existe_archivo:
                escritor.writerow(["timestamp", "par", "tipo", "precio", "cantidad", "motivo"])
            escritor.writerow(
                [datetime.now(timezone.utc).isoformat(), par, tipo, precio, cantidad, motivo]
            )
    except OSError as error:
        logger.error(f"Error escribiendo en {config.ARCHIVO_LOG_OPERACIONES}: {error}")


# =============================================================================
# ESTADO COMPARTIDO ENTRE LAS CORRUTINAS DE CADA PAR
# =============================================================================

@dataclass
class EstadoBot:
    api_key: str
    api_secret: str
    supabase: object
    posiciones: dict = field(default_factory=dict)       # pair -> posicion abierta
    capital_por_par: dict = field(default_factory=dict)  # pair -> capital compuesto actual
    lock: threading.Lock = field(default_factory=threading.Lock)


# =============================================================================
# ANALISIS POR PAR (unidad de trabajo async, corre concurrente para los 3)
# =============================================================================

async def analizar_par(par: str, estado: EstadoBot) -> None:
    logger.info(f"--- Analizando {par} ---")
    try:
        posicion = estado.posiciones.get(par)

        if posicion is not None:
            precio_actual = await asyncio.to_thread(obtener_precio_kraken, par)
            motivo = verificar_salida(posicion, precio_actual, datetime.now(timezone.utc))

            if motivo:
                orden = await asyncio.to_thread(
                    crear_orden_kraken, estado.api_key, estado.api_secret, par, "sell", posicion["cantidad"]
                )
                if orden:
                    ganancia = (precio_actual - posicion["precio_entrada"]) * posicion["cantidad"]
                    ganancia_pct = (precio_actual - posicion["precio_entrada"]) / posicion["precio_entrada"] * 100
                    duracion_minutos = int(
                        (datetime.now(timezone.utc) - posicion["timestamp_entrada"]).total_seconds() / 60
                    )

                    registrar_operacion(par, "VENTA", precio_actual, posicion["cantidad"], motivo)

                    await asyncio.to_thread(
                        supabase_manager.cerrar_trade,
                        estado.supabase, posicion["trade_id"], precio_actual, ganancia_pct, motivo,
                    )
                    await asyncio.to_thread(
                        supabase_manager.cerrar_sesion,
                        estado.supabase, posicion["session_id"], ganancia, motivo,
                    )

                    with estado.lock:
                        estado.capital_por_par[par] = max(
                            config.CAPITAL_FLOOR_USD, estado.capital_por_par.get(par, config.CAPITAL_FLOOR_USD) + ganancia
                        )
                        del estado.posiciones[par]

                    enviar_telegram_salida(
                        par, posicion["precio_entrada"], precio_actual, ganancia, ganancia_pct, motivo, duracion_minutos
                    )
            else:
                logger.info(
                    f"[{par}] Posicion abierta sin cambios. "
                    f"Precio actual: {precio_actual:.2f}, entrada: {posicion['precio_entrada']:.2f}"
                )
            return

        df = await asyncio.to_thread(obtener_klines_kraken, par)
        df = calculate_indicators(df)
        df["atr"] = calcular_atr(df)

        ultima_vela = df.iloc[-1]
        atr_pct = ultima_vela["atr"] / ultima_vela["cierre"] if ultima_vela["cierre"] else float("nan")
        logger.info(
            f"[{par}] Cierre: {ultima_vela['cierre']:.2f} | RSI: {ultima_vela['rsi']:.2f} | "
            f"MACD: {ultima_vela['macd']:.4f} | Senal MACD: {ultima_vela['macd_senal']:.4f} | "
            f"ATR%: {atr_pct:.4f}"
        )

        if senal_entrada(df, par):
            capital_asignado = estado.capital_por_par.get(par, config.CAPITAL_FLOOR_USD)
            balance_disponible = await asyncio.to_thread(
                obtener_balance_kraken, estado.api_key, estado.api_secret, config.ACTIVO_KRAKEN
            )
            monto_a_usar = min(capital_asignado, balance_disponible)

            if monto_a_usar <= 0:
                logger.warning(f"[{par}] Sin balance {config.MONEDA_CAPITAL} disponible para comprar.")
                return

            precio_entrada = float(ultima_vela["cierre"])
            cantidad = await asyncio.to_thread(calcular_cantidad_compra_kraken, par, monto_a_usar, precio_entrada)
            if cantidad <= 0:
                return

            orden = await asyncio.to_thread(
                crear_orden_kraken, estado.api_key, estado.api_secret, par, "buy", cantidad
            )
            if orden:
                session_id = await asyncio.to_thread(
                    supabase_manager.crear_sesion, estado.supabase, par, capital_asignado
                )
                trade_id = await asyncio.to_thread(
                    supabase_manager.crear_trade,
                    estado.supabase, session_id, par, precio_entrada, cantidad,
                )

                with estado.lock:
                    estado.posiciones[par] = {
                        "session_id": session_id,
                        "trade_id": trade_id,
                        "precio_entrada": precio_entrada,
                        "cantidad": cantidad,
                        "capital_usado": capital_asignado,
                        "timestamp_entrada": datetime.now(timezone.utc),
                    }

                registrar_operacion(par, "COMPRA", precio_entrada, cantidad, "Senal RSI+MACD+ATR")
                enviar_telegram_entrada(par, precio_entrada, cantidad, capital_asignado)
        else:
            logger.info(f"[{par}] Sin senal de entrada por ahora.")

    except (requests.RequestException, RuntimeError) as error:
        logger.error(f"[{par}] Error de API de Kraken: {error}")
        enviar_telegram(f"⚠️ Error de API de Kraken analizando {formatear_par(par)}: {error}")
    except Exception as error:
        logger.exception(f"[{par}] Error inesperado durante el analisis: {error}")


async def ciclo_analisis(estado: EstadoBot) -> None:
    """Analiza los 3 pares en paralelo; un error en uno no debe tumbar a los otros."""
    resultados = await asyncio.gather(
        *(analizar_par(par, estado) for par in config.PARES),
        return_exceptions=True,
    )
    for par, resultado in zip(config.PARES, resultados):
        if isinstance(resultado, Exception):
            logger.exception(f"[{par}] Excepcion no capturada en el ciclo: {resultado}")


# =============================================================================
# PUNTO DE ENTRADA: LOOP PRINCIPAL ASYNC DEL BOT
# =============================================================================

async def main_async() -> None:
    logger.info("=== Iniciando bot MULTI-PAR de trading en Kraken ===")

    if config.MODO_SIMULACION:
        logger.warning("MODO SIMULACION activo: NO se van a mandar ordenes reales a Kraken.")
    else:
        logger.warning("MODO REAL activo: el bot va a operar con dinero real en Kraken.")

    api_key, api_secret = cargar_config_kraken()
    supabase = supabase_manager.obtener_cliente_supabase()

    posiciones = supabase_manager.obtener_posiciones_abiertas(supabase)
    capital_por_par = {
        par: supabase_manager.obtener_capital_por_par(supabase, par) for par in config.PARES
    }

    estado = EstadoBot(
        api_key=api_key,
        api_secret=api_secret,
        supabase=supabase,
        posiciones=posiciones,
        capital_por_par=capital_por_par,
    )

    resumen_capital = ", ".join(f"{formatear_par(p)}: ${c:.2f}" for p, c in capital_por_par.items())
    enviar_telegram(
        f"🤖 Bot MULTI-PAR de Kraken iniciado ({'SIMULACION' if config.MODO_SIMULACION else 'REAL'})\n"
        f"Pares: {', '.join(formatear_par(p) for p in config.PARES)}\n"
        f"Capital por par: {resumen_capital}\n"
        f"Posiciones abiertas reconstruidas: {len(posiciones)}\n"
        f"Take Profit: {config.PROFIT_TARGET * 100:.0f}% | Stop Loss: {config.STOP_LOSS * 100:.0f}% | "
        f"Timeout: {config.TIMEOUT_HORAS}h"
    )

    try:
        while True:
            try:
                await ciclo_analisis(estado)
                await asyncio.to_thread(supabase_manager.actualizar_heartbeat, estado.supabase)
            except Exception as error:
                logger.exception(f"Error critico en el ciclo principal: {error}")
                enviar_telegram(f"❌ Error critico en el bot multi-par de Kraken: {error}")

            logger.info(f"Esperando {config.INTERVALO_ANALISIS_SEGUNDOS} segundos para el proximo analisis...")
            await asyncio.sleep(config.INTERVALO_ANALISIS_SEGUNDOS)
    except KeyboardInterrupt:
        logger.info("Señal de interrupción recibida (Ctrl+C). Cerrando...")
        with estado.lock:
            hay_posiciones = bool(estado.posiciones)

        if hay_posiciones:
            logger.warning(
                "Hay posiciones abiertas: quedan registradas en sessions_multi/trades_multi "
                "y se retoman automaticamente al reiniciar el bot."
            )
            enviar_telegram(
                "⚠️ Bot MULTI-PAR de Kraken detenido con posiciones abiertas: "
                "se retoman desde Supabase al reiniciar."
            )
        else:
            enviar_telegram("🛑 Bot MULTI-PAR de Kraken detenido (sin posiciones abiertas).")

        logger.info("Bot detenido.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
