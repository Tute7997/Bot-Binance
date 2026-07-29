"""
Bot de trading para Kraken (dinero REAL). Misma estrategia RSI + MACD y misma
logica de TP/SL que el bot de Binance Testnet (Bot Binance/main.py).

Sistema de sesiones: en vez de un capital fijo por variable de entorno, cada
sesion arranca con el balance real de Kraken como capital inicial (tabla
"sessions" en Supabase). El profit de cada sesion se acumula al terminar en
"account_state.capital_depositado", que sirve de referencia para la proxima.

El reinicio del bot al iniciar/cerrar una sesion desde el dashboard es
MANUAL: los botones del dashboard solo crean/cierran la fila en Supabase,
no hay un process manager que reinicie este script solo. Hay que parar
(Ctrl+C) y volver a correr "python kraken-bot.py" para que tome los cambios.

Al recibir Ctrl+C, el bot intenta cerrar la sesion activa en Supabase, pero
SOLO si no quedan posiciones abiertas (si hay una posicion abierta, la
sesion sigue activa para no perder el seguimiento del profit real).

Arranca en modo simulacion por defecto (KRAKEN_MODO_SIMULACION=true en
.env.local): analiza y avisa por Telegram que orden HARIA, pero no manda
nada real a Kraken. Cambiar esa variable a false para operar con dinero real.
"""

import base64
import csv
import hashlib
import hmac
import json
import logging
import os
import threading
import time
import urllib.parse
import uuid
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv
from supabase import create_client

# =============================================================================
# CONFIGURACION Y PARAMETROS DE LA ESTRATEGIA
# =============================================================================

load_dotenv(dotenv_path=".env.local")

# Pares que el bot va a analizar y operar (formato Kraken).
# Nota: Kraken NO tiene pares cotizados en ARS (verificado en vivo contra
# /0/public/AssetPairs), asi que el capital y el balance se manejan en USD
# (activo Kraken "ZUSD").
PARES = ["XXBTZUSD", "XETHZUSD"]
MONEDA_CAPITAL = "USD"
ACTIVO_KRAKEN = "ZUSD"

# Nombres "lindos" para mostrar en los mensajes (Kraken usa nomenclatura legacy)
NOMBRES_PARES = {"XXBTZUSD": "BTC/USD", "XETHZUSD": "ETH/USD"}

# Si es True, el bot NUNCA manda ordenes reales: solo loguea/avisa que haria
KRAKEN_MODO_SIMULACION = os.getenv("KRAKEN_MODO_SIMULACION", "true").strip().lower() == "true"

# Si es True, loguea cada peticion privada a Kraken (API-Key enmascarada,
# nunca la Private Key, status y body de la respuesta). Apagado por defecto.
KRAKEN_DEBUG_AUTH = os.getenv("KRAKEN_DEBUG_AUTH", "false").strip().lower() == "true"

# Objetivo de ganancia (take profit) y limite de perdida (stop loss)
PROFIT_TARGET = 0.05  # +5%
STOP_LOSS = 0.03  # -3%

# Parametros de los indicadores tecnicos (identicos a Bot Binance/main.py)
RSI_PERIODO = 14
RSI_UMBRAL = 30
MACD_RAPIDA = 12
MACD_LENTA = 26
MACD_SENAL = 9

# Configuracion de velas y frecuencia de analisis
INTERVALO_VELA_MINUTOS = 1
INTERVALO_ANALISIS_SEGUNDOS = 60
INTERVALO_RESUMEN_SEGUNDOS = 1800  # 30 minutos

# Endpoint base de la API real de Kraken
KRAKEN_BASE_URL = "https://api.kraken.com"

# Archivos de estado y de registro
ARCHIVO_POSICIONES = "posiciones_kraken.json"
ARCHIVO_LOG_OPERACIONES = "operaciones_kraken.csv"
ARCHIVO_LOG_BOT = "kraken_bot.log"

# Nombres de tablas en Supabase (usadas por el dashboard)
TABLA_TRADES = "trades_kraken"
TABLA_HEARTBEAT = "kraken_heartbeat"
TABLA_SESSIONS = "sessions"
TABLA_ACCOUNT_STATE = "account_state"
HEARTBEAT_ID = 1
ACCOUNT_STATE_ID = 1

# Protege el dict "posiciones" porque lo tocan el loop principal (60s) y el
# hilo de resumen periodico (30 min) al mismo tiempo.
posiciones_lock = threading.Lock()


# =============================================================================
# CONFIGURACION DE LOGGING (consola + archivo)
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(ARCHIVO_LOG_BOT, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("kraken_bot")


# =============================================================================
# CARGA DE CONFIGURACION
# =============================================================================

def cargar_config_kraken():
    """Obtiene las credenciales de Kraken desde .env.local."""
    load_dotenv(dotenv_path=".env.local")

    api_key = os.getenv("KRAKEN_API_KEY")
    api_secret = os.getenv("KRAKEN_PRIVATE_KEY")

    if not api_key or not api_secret:
        raise ValueError("Faltan KRAKEN_API_KEY o KRAKEN_PRIVATE_KEY en .env.local")

    logger.info("Credenciales de Kraken cargadas correctamente.")
    return api_key, api_secret


def cargar_cliente_supabase():
    """Crea el cliente de Supabase (mismo proyecto que usan Bot Binance y Bot Ripio)."""
    load_dotenv(dotenv_path=".env.local")

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        logger.warning(
            "Faltan SUPABASE_URL o SUPABASE_KEY en .env.local. "
            "El dashboard no va a recibir datos del bot."
        )
        return None

    try:
        cliente_supabase = create_client(url, key)
        logger.info("Cliente de Supabase inicializado correctamente.")
        return cliente_supabase
    except Exception as error:
        logger.error(f"Error inicializando cliente de Supabase: {error}")
        return None


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
    return NOMBRES_PARES.get(simbolo, simbolo)


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
    data["nonce"] = str(int(time.time() * 1000))
    firma = firmar_kraken(path, data, api_secret)
    headers = {"API-Key": api_key, "API-Sign": firma}

    if KRAKEN_DEBUG_AUTH:
        api_key_enmascarada = f"{api_key[:8]}..." if api_key else None
        logger.info(f"[KRAKEN_DEBUG_AUTH] POST {path} | API-Key={api_key_enmascarada} | nonce={data['nonce']}")

    respuesta = requests.post(f"{KRAKEN_BASE_URL}{path}", headers=headers, data=data, timeout=10)

    if KRAKEN_DEBUG_AUTH:
        logger.info(f"[KRAKEN_DEBUG_AUTH] Respuesta {respuesta.status_code}: {respuesta.text[:300]}")

    respuesta.raise_for_status()
    datos = respuesta.json()

    if datos.get("error"):
        raise RuntimeError(f"Kraken devolvio un error: {datos['error']}")

    return datos.get("result", {})


def obtener_balance_kraken(api_key: str, api_secret: str, activo: str = ACTIVO_KRAKEN) -> float:
    """Obtiene el saldo de un activo en la cuenta de Kraken."""
    try:
        resultado = peticion_privada_kraken("/0/private/Balance", api_key, api_secret)
        return float(resultado.get(activo, 0))
    except (requests.RequestException, RuntimeError) as error:
        logger.error(f"Error obteniendo balance de {activo} en Kraken: {error}")
        return 0.0


def obtener_precio_kraken(par: str) -> float:
    """Obtiene el ultimo precio operado de un par (endpoint publico)."""
    respuesta = requests.get(f"{KRAKEN_BASE_URL}/0/public/Ticker", params={"pair": par}, timeout=10)
    respuesta.raise_for_status()
    datos = respuesta.json()

    if datos.get("error"):
        raise RuntimeError(f"Kraken devolvio un error: {datos['error']}")

    resultado = datos["result"]
    clave_par = next(iter(resultado))
    return float(resultado[clave_par]["c"][0])


def obtener_klines_kraken(par: str) -> pd.DataFrame:
    """
    Obtiene velas historicas reales de Kraken (a diferencia de Ripio, este
    endpoint publico si las provee) y arma un DataFrame con columna 'cierre'.
    Se descarta la ultima vela porque todavia esta en formacion.
    """
    respuesta = requests.get(
        f"{KRAKEN_BASE_URL}/0/public/OHLC",
        params={"pair": par, "interval": INTERVALO_VELA_MINUTOS},
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

    logger.info(f"[{par}] Se obtuvieron {len(df)} velas cerradas de {INTERVALO_VELA_MINUTOS}m.")
    return df


def obtener_precision_kraken(par: str) -> tuple:
    """Obtiene la cantidad de decimales permitida y el volumen minimo para un par."""
    respuesta = requests.get(f"{KRAKEN_BASE_URL}/0/public/AssetPairs", params={"pair": par}, timeout=10)
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
    KRAKEN_MODO_SIMULACION esta activo, NO manda nada real: solo loguea y
    avisa por Telegram que orden se habria hecho.
    """
    accion = "comprado" if tipo == "buy" else "vendido"

    if KRAKEN_MODO_SIMULACION:
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
# SISTEMA DE SESIONES (Supabase: "sessions" + "account_state")
# =============================================================================

def obtener_estado_cuenta_supabase(supabase):
    """Lee la fila unica de account_state, o None si falla/no esta configurado."""
    if supabase is None:
        return None
    try:
        respuesta = supabase.table(TABLA_ACCOUNT_STATE).select("*").eq("id", ACCOUNT_STATE_ID).maybe_single().execute()
        return respuesta.data
    except Exception as error:
        logger.error(f"Error leyendo account_state: {error}")
        return None


def crear_sesion_supabase(supabase, capital_inicial: float):
    """
    Crea una nueva sesion (fila en "sessions") y actualiza account_state.
    Si capital_depositado todavia esta en 0 (primera sesion de la cuenta),
    se inicializa con este mismo capital_inicial.
    """
    if supabase is None:
        return None

    session_id = str(uuid.uuid4())
    try:
        supabase.table(TABLA_SESSIONS).insert({
            "session_id": session_id,
            "capital_inicial": capital_inicial,
            "status": "active",
        }).execute()

        estado = obtener_estado_cuenta_supabase(supabase)
        capital_depositado_actual = float(estado.get("capital_depositado") or 0) if estado else 0.0
        nuevo_capital_depositado = capital_depositado_actual if capital_depositado_actual > 0 else capital_inicial

        supabase.table(TABLA_ACCOUNT_STATE).upsert({
            "id": ACCOUNT_STATE_ID,
            "capital_depositado": nuevo_capital_depositado,
            "capital_operando": capital_inicial,
            "session_activa": session_id,
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()

        logger.info(f"Nueva sesion creada: {session_id} (capital inicial: {capital_inicial:.2f} {MONEDA_CAPITAL})")
        return session_id
    except Exception as error:
        logger.error(f"Error creando la sesion en Supabase: {error}")
        return None


def cerrar_sesion_supabase(supabase, session_id: str, capital_final: float):
    """Cierra la sesion en Supabase y acumula el profit en account_state."""
    if supabase is None or session_id is None:
        return None

    try:
        sesion = supabase.table(TABLA_SESSIONS).select("capital_inicial").eq("session_id", session_id).single().execute()
        capital_inicial = float(sesion.data["capital_inicial"])
        profit = capital_final - capital_inicial
        profit_percent = (profit / capital_inicial * 100) if capital_inicial > 0 else 0.0

        supabase.table(TABLA_SESSIONS).update({
            "end_time": datetime.utcnow().isoformat(),
            "capital_final": capital_final,
            "profit": profit,
            "profit_percent": profit_percent,
            "status": "closed",
        }).eq("session_id", session_id).execute()

        estado = obtener_estado_cuenta_supabase(supabase) or {}
        capital_depositado_actual = float(estado.get("capital_depositado") or 0)

        supabase.table(TABLA_ACCOUNT_STATE).update({
            "capital_depositado": capital_depositado_actual + profit,
            "capital_operando": 0,
            "session_activa": None,
            "updated_at": datetime.utcnow().isoformat(),
        }).eq("id", ACCOUNT_STATE_ID).execute()

        logger.info(f"Sesion {session_id} cerrada. Profit: {profit:+.2f} {MONEDA_CAPITAL} ({profit_percent:+.2f}%)")
        return {"capital_inicial": capital_inicial, "profit": profit, "profit_percent": profit_percent}
    except Exception as error:
        logger.error(f"Error cerrando la sesion {session_id} en Supabase: {error}")
        return None


def resolver_sesion_activa(supabase, api_key: str, api_secret: str):
    """
    Si ya hay una sesion activa en account_state (por ejemplo, iniciada desde
    el dashboard), la adopta. Si no, crea una nueva usando el balance real
    actual de Kraken como capital inicial.
    """
    estado = obtener_estado_cuenta_supabase(supabase)

    if estado and estado.get("session_activa"):
        session_id = estado["session_activa"]
        try:
            sesion = supabase.table(TABLA_SESSIONS).select("capital_inicial").eq("session_id", session_id).single().execute()
            capital_inicial = float(sesion.data["capital_inicial"])
            logger.info(f"Sesion activa encontrada ({session_id}), capital inicial: {capital_inicial:.2f} {MONEDA_CAPITAL}")
            return session_id, capital_inicial
        except Exception as error:
            logger.error(f"No se pudo leer la sesion activa {session_id}, se crea una nueva: {error}")

    balance_actual = obtener_balance_kraken(api_key, api_secret, ACTIVO_KRAKEN)
    session_id = crear_sesion_supabase(supabase, balance_actual)
    return session_id, balance_actual


def intentar_cerrar_sesion(supabase, session_id, posiciones: dict, api_key: str, api_secret: str) -> None:
    """
    Se llama al recibir Ctrl+C. Si hay posiciones abiertas, NO cierra la
    sesion (para no perder el seguimiento del profit real) y solo avisa.
    """
    if supabase is None or session_id is None:
        return

    with posiciones_lock:
        hay_posiciones = bool(posiciones)

    if hay_posiciones:
        logger.warning(
            "Hay posiciones abiertas: la sesion NO se cierra en Supabase. "
            "Si volves a correr el bot, va a retomar esta misma sesion."
        )
        enviar_telegram("⚠️ Bot detenido con posiciones abiertas: la sesión sigue activa en Supabase.")
        return

    balance_final = obtener_balance_kraken(api_key, api_secret, ACTIVO_KRAKEN)
    resultado = cerrar_sesion_supabase(supabase, session_id, balance_final)

    if resultado:
        enviar_telegram(
            f"🛑 Sesión cerrada (Kraken)\n"
            f"Capital inicial: ${resultado['capital_inicial']:,.2f} {MONEDA_CAPITAL}\n"
            f"Capital final: ${balance_final:,.2f} {MONEDA_CAPITAL}\n"
            f"Profit: {resultado['profit']:+.2f} {MONEDA_CAPITAL} ({resultado['profit_percent']:+.2f}%)"
        )


# =============================================================================
# CALCULO DE INDICADORES: RSI Y MACD (identico a Bot Binance/main.py)
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

    media_ganancia = ganancia.ewm(alpha=1 / RSI_PERIODO, min_periods=RSI_PERIODO, adjust=False).mean()
    media_perdida = perdida.ewm(alpha=1 / RSI_PERIODO, min_periods=RSI_PERIODO, adjust=False).mean()

    fuerza_relativa = media_ganancia / media_perdida
    df["rsi"] = 100 - (100 / (1 + fuerza_relativa))
    df["rsi"] = df["rsi"].where(media_perdida != 0, 100)

    ema_rapida = cierre.ewm(span=MACD_RAPIDA, adjust=False).mean()
    ema_lenta = cierre.ewm(span=MACD_LENTA, adjust=False).mean()
    df["macd"] = ema_rapida - ema_lenta
    df["macd_senal"] = df["macd"].ewm(span=MACD_SENAL, adjust=False).mean()
    df["macd_histograma"] = df["macd"] - df["macd_senal"]

    return df


def buy_signal(df: pd.DataFrame) -> bool:
    """RSI < RSI_UMBRAL Y el MACD cruzo al alza en la ultima vela cerrada."""
    if len(df) < 2:
        return False

    vela_actual = df.iloc[-1]
    vela_anterior = df.iloc[-2]

    if pd.isna(vela_actual["rsi"]) or pd.isna(vela_actual["macd"]):
        return False

    rsi_en_sobreventa = vela_actual["rsi"] < RSI_UMBRAL
    cruce_alcista_macd = (
        vela_anterior["macd"] <= vela_anterior["macd_senal"]
        and vela_actual["macd"] > vela_actual["macd_senal"]
    )

    if rsi_en_sobreventa and cruce_alcista_macd:
        logger.info(
            f"Senal de compra detectada -> RSI={vela_actual['rsi']:.2f}, "
            f"MACD={vela_actual['macd']:.4f}, Senal={vela_actual['macd_senal']:.4f}"
        )
        return True

    return False


# =============================================================================
# PERSISTENCIA DE POSICIONES ABIERTAS
# =============================================================================

def cargar_posiciones() -> dict:
    if not os.path.exists(ARCHIVO_POSICIONES):
        return {}

    try:
        with open(ARCHIVO_POSICIONES, "r", encoding="utf-8") as archivo:
            posiciones = json.load(archivo)
        logger.info(f"Posiciones cargadas desde {ARCHIVO_POSICIONES}: {list(posiciones.keys())}")
        return posiciones
    except (json.JSONDecodeError, OSError) as error:
        logger.error(f"Error cargando {ARCHIVO_POSICIONES}: {error}. Se inicia sin posiciones.")
        return {}


def guardar_posiciones(posiciones: dict) -> None:
    try:
        with open(ARCHIVO_POSICIONES, "w", encoding="utf-8") as archivo:
            json.dump(posiciones, archivo, indent=2, ensure_ascii=False)
    except OSError as error:
        logger.error(f"Error guardando {ARCHIVO_POSICIONES}: {error}")


def verificar_salida(posicion: dict, precio_actual: float):
    """Devuelve "TP", "SL" o None segun PROFIT_TARGET/STOP_LOSS."""
    precio_entrada = posicion["precio_entrada"]
    variacion = (precio_actual - precio_entrada) / precio_entrada

    if variacion >= PROFIT_TARGET:
        return "TP"
    if variacion <= -STOP_LOSS:
        return "SL"
    return None


# =============================================================================
# ALERTAS DE TELEGRAM PARA APERTURA / CIERRE / RESUMEN DE POSICIONES
# =============================================================================

def enviar_telegram_trade_abierto(par: str, precio_entrada: float, cantidad: float) -> None:
    target = precio_entrada * (1 + PROFIT_TARGET)
    stop = precio_entrada * (1 - STOP_LOSS)
    hora = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    prefijo = "🧪 [SIMULACIÓN] " if KRAKEN_MODO_SIMULACION else ""

    enviar_telegram(
        f"{prefijo}🟢 OPERACIÓN ABIERTA (Kraken)\n"
        f"Par: {formatear_par(par)}\n"
        f"Entrada: ${precio_entrada:,.2f} {MONEDA_CAPITAL}\n"
        f"Cantidad: {cantidad}\n"
        f"Target: ${target:,.2f} (+{PROFIT_TARGET * 100:.0f}%)\n"
        f"Stop Loss: ${stop:,.2f} (-{STOP_LOSS * 100:.0f}%)\n"
        f"Hora: {hora}"
    )


def enviar_telegram_resumen_posiciones(posiciones: dict) -> None:
    """Resumen periodico de posiciones abiertas en Kraken (no manda nada si no hay ninguna)."""
    with posiciones_lock:
        snapshot = dict(posiciones)

    if not snapshot:
        return

    lineas = ["📊 RESUMEN OPERACIONES ABIERTAS (Kraken)", ""]
    numeros = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]
    profit_total = 0.0

    for indice, (par, posicion) in enumerate(snapshot.items()):
        try:
            precio_actual = obtener_precio_kraken(par)
        except (requests.RequestException, RuntimeError) as error:
            logger.error(f"[{par}] Error obteniendo precio para el resumen: {error}")
            continue

        precio_entrada = posicion["precio_entrada"]
        profit_pct = (precio_actual - precio_entrada) / precio_entrada * 100
        profit = (precio_actual - precio_entrada) * posicion["cantidad"]
        profit_total += profit

        target = precio_entrada * (1 + PROFIT_TARGET)
        stop = precio_entrada * (1 - STOP_LOSS)
        minutos_abierto = int(
            (datetime.now() - datetime.fromisoformat(posicion["timestamp"])).total_seconds() / 60
        )

        etiqueta = numeros[indice] if indice < len(numeros) else f"{indice + 1}."
        lineas.append(f"{etiqueta} {formatear_par(par)}")
        lineas.append(f"  Entrada: ${precio_entrada:,.2f} | Profit actual: {profit_pct:+.1f}%")
        lineas.append(f"  Target: ${target:,.2f} | Stop: ${stop:,.2f}")
        lineas.append(f"  Tiempo abierto: {formatear_duracion(minutos_abierto)}")
        lineas.append("")

    lineas.append("═" * 31)
    lineas.append(f"Total abierto: {len(snapshot)} operaciones")
    lineas.append(f"Profit total actual: {'+' if profit_total >= 0 else '-'}${abs(profit_total):.2f} {MONEDA_CAPITAL}")

    enviar_telegram("\n".join(lineas))


def hilo_resumen_periodico(posiciones: dict) -> None:
    """Hilo de fondo que manda el resumen de posiciones abiertas cada 30 minutos."""
    while True:
        time.sleep(INTERVALO_RESUMEN_SEGUNDOS)
        try:
            enviar_telegram_resumen_posiciones(posiciones)
        except Exception as error:
            logger.error(f"Error enviando el resumen periodico de posiciones: {error}")


# =============================================================================
# REGISTRO DE TRADES EN SUPABASE (para el dashboard web)
# =============================================================================

def registrar_trade_apertura_supabase(supabase, par: str, precio_entrada: float, cantidad: float):
    """
    Inserta una fila en 'trades_kraken' al abrir una posicion y manda el
    aviso de Telegram. Devuelve el id insertado (o None si Supabase falla
    o no esta configurado; el Telegram se manda igual).
    """
    trade_id = None

    if supabase is not None:
        try:
            respuesta = supabase.table(TABLA_TRADES).insert({
                "pair": par,
                "side": "BUY",
                "entry_price": precio_entrada,
                "quantity": cantidad,
                "status": "open",
            }).execute()

            trade_id = respuesta.data[0]["id"]
            logger.info(f"[{par}] Trade registrado en Supabase (id={trade_id}).")
        except Exception as error:
            logger.error(f"[{par}] Error insertando trade en Supabase: {error}")

    enviar_telegram_trade_abierto(par, precio_entrada, cantidad)
    return trade_id


def actualizar_trade_cierre_supabase(
    supabase,
    trade_id,
    par: str,
    precio_entrada: float,
    precio_salida: float,
    ganancia: float,
    ganancia_pct: float,
    motivo: str,
    duracion_minutos: int,
) -> None:
    """Actualiza el trade cerrado en Supabase y manda el aviso de Telegram (siempre)."""
    if supabase is not None and trade_id is not None:
        try:
            supabase.table(TABLA_TRADES).update({
                "exit_price": precio_salida,
                "profit": ganancia,
                "profit_percent": ganancia_pct,
                "status": "closed",
                "reason": motivo,
            }).eq("id", trade_id).execute()
            logger.info(f"Trade {trade_id} actualizado en Supabase (cierre {motivo}).")
        except Exception as error:
            logger.error(f"Error actualizando trade {trade_id} en Supabase: {error}")

    texto_motivo = "Take Profit" if motivo == "TP" else "Stop Loss"
    signo = "+" if ganancia >= 0 else "-"
    prefijo = "🧪 [SIMULACIÓN] " if KRAKEN_MODO_SIMULACION else ""

    enviar_telegram(
        f"{prefijo}✅ OPERACIÓN CERRADA (Kraken)\n"
        f"Par: {formatear_par(par)}\n"
        f"Entrada: ${precio_entrada:,.2f} {MONEDA_CAPITAL}\n"
        f"Salida: ${precio_salida:,.2f} {MONEDA_CAPITAL}\n"
        f"Ganancia: {signo}${abs(ganancia):.2f} {MONEDA_CAPITAL} ({ganancia_pct:+.2f}%)\n"
        f"Motivo: {texto_motivo}\n"
        f"Duración: {formatear_duracion(duracion_minutos)}"
    )


def actualizar_heartbeat_supabase(supabase) -> None:
    """Actualiza (upsert) la fila de heartbeat para que el dashboard sepa si el bot esta activo."""
    if supabase is None:
        return

    try:
        supabase.table(TABLA_HEARTBEAT).upsert({
            "id": HEARTBEAT_ID,
            "last_check": datetime.utcnow().isoformat(),
        }).execute()
    except Exception as error:
        logger.error(f"Error actualizando heartbeat en Supabase: {error}")


# =============================================================================
# REGISTRO DE OPERACIONES (LOG DE TRADES EN CSV)
# =============================================================================

def registrar_operacion(par: str, tipo: str, precio: float, cantidad: float, motivo: str) -> None:
    existe_archivo = os.path.exists(ARCHIVO_LOG_OPERACIONES)

    try:
        with open(ARCHIVO_LOG_OPERACIONES, "a", newline="", encoding="utf-8") as archivo:
            escritor = csv.writer(archivo)
            if not existe_archivo:
                escritor.writerow(["timestamp", "par", "tipo", "precio", "cantidad", "motivo"])
            escritor.writerow(
                [datetime.now().isoformat(), par, tipo, precio, cantidad, motivo]
            )
    except OSError as error:
        logger.error(f"Error escribiendo en {ARCHIVO_LOG_OPERACIONES}: {error}")


# =============================================================================
# ANALISIS PRINCIPAL (SE EJECUTA CADA MINUTO PARA CADA PAR)
# =============================================================================

def main_analysis(posiciones: dict, supabase, api_key: str, api_secret: str, capital_por_par: dict) -> None:
    """
    Recorre cada par: si hay posicion abierta, revisa TP/SL contra el precio
    en vivo; si no, trae velas reales de Kraken y evalua senal de compra
    (RSI+MACD), igual que Bot Binance/main.py. "capital_por_par" viene de la
    sesion activa (ver resolver_sesion_activa en main()).
    """
    for par in PARES:
        logger.info(f"--- Analizando {par} ---")
        try:
            if par in posiciones:
                precio_actual = obtener_precio_kraken(par)
                posicion = posiciones[par]
                motivo_salida = verificar_salida(posicion, precio_actual)

                if motivo_salida:
                    orden = crear_orden_kraken(api_key, api_secret, par, "sell", posicion["cantidad"])
                    if orden:
                        ganancia_pct = (precio_actual - posicion["precio_entrada"]) / posicion["precio_entrada"] * 100
                        ganancia = (precio_actual - posicion["precio_entrada"]) * posicion["cantidad"]
                        texto_motivo = "Take Profit alcanzado" if motivo_salida == "TP" else "Stop Loss alcanzado"
                        duracion_minutos = int(
                            (datetime.now() - datetime.fromisoformat(posicion["timestamp"])).total_seconds() / 60
                        )

                        registrar_operacion(par, "VENTA", precio_actual, posicion["cantidad"], texto_motivo)
                        actualizar_trade_cierre_supabase(
                            supabase, posicion.get("trade_id_supabase"),
                            par, posicion["precio_entrada"], precio_actual,
                            ganancia, ganancia_pct, motivo_salida, duracion_minutos,
                        )

                        with posiciones_lock:
                            del posiciones[par]
                        guardar_posiciones(posiciones)
                else:
                    logger.info(
                        f"[{par}] Posicion abierta sin cambios. "
                        f"Precio actual: {precio_actual:.2f}, entrada: {posicion['precio_entrada']:.2f}"
                    )
                continue

            df = obtener_klines_kraken(par)
            df = calculate_indicators(df)
            ultima_vela = df.iloc[-1]
            logger.info(
                f"[{par}] Cierre: {ultima_vela['cierre']:.2f} | "
                f"RSI: {ultima_vela['rsi']:.2f} | MACD: {ultima_vela['macd']:.4f} | "
                f"Senal MACD: {ultima_vela['macd_senal']:.4f}"
            )

            if buy_signal(df):
                monto_asignado = capital_por_par[par]
                balance_disponible = obtener_balance_kraken(api_key, api_secret, ACTIVO_KRAKEN)
                monto_a_usar = min(monto_asignado, balance_disponible)

                if monto_a_usar <= 0:
                    logger.warning(f"[{par}] Sin balance {MONEDA_CAPITAL} disponible para comprar.")
                    continue

                precio_entrada = float(ultima_vela["cierre"])
                cantidad = calcular_cantidad_compra_kraken(par, monto_a_usar, precio_entrada)
                if cantidad <= 0:
                    continue

                orden = crear_orden_kraken(api_key, api_secret, par, "buy", cantidad)
                if orden:
                    trade_id_supabase = registrar_trade_apertura_supabase(
                        supabase, par, precio_entrada, cantidad
                    )
                    with posiciones_lock:
                        posiciones[par] = {
                            "precio_entrada": precio_entrada,
                            "cantidad": cantidad,
                            "timestamp": datetime.now().isoformat(),
                            "trade_id_supabase": trade_id_supabase,
                        }
                    guardar_posiciones(posiciones)

                    registrar_operacion(par, "COMPRA", precio_entrada, cantidad, "Senal RSI+MACD")
            else:
                logger.info(f"[{par}] Sin senal de compra por ahora.")

        except (requests.RequestException, RuntimeError) as error:
            logger.error(f"[{par}] Error de API de Kraken: {error}")
            enviar_telegram(f"⚠️ Error de API de Kraken analizando {formatear_par(par)}: {error}")
        except Exception as error:
            logger.exception(f"[{par}] Error inesperado durante el analisis: {error}")


# =============================================================================
# PUNTO DE ENTRADA: LOOP PRINCIPAL DEL BOT
# =============================================================================

def main() -> None:
    logger.info("=== Iniciando bot de trading en Kraken ===")

    if KRAKEN_MODO_SIMULACION:
        logger.warning("MODO SIMULACION activo: NO se van a mandar ordenes reales a Kraken.")
    else:
        logger.warning("MODO REAL activo: el bot va a operar con dinero real en Kraken.")

    api_key, api_secret = cargar_config_kraken()
    supabase = cargar_cliente_supabase()
    posiciones = cargar_posiciones()

    session_id, capital_sesion = resolver_sesion_activa(supabase, api_key, api_secret)
    capital_por_par = {par: capital_sesion / len(PARES) for par in PARES}
    logger.info(f"Capital de la sesion: {capital_sesion:.2f} {MONEDA_CAPITAL} (por par: {capital_por_par})")

    enviar_telegram(
        f"🤖 Bot de trading iniciado en Kraken ({'SIMULACION' if KRAKEN_MODO_SIMULACION else 'REAL'})\n"
        f"Sesión: {session_id or 'sin Supabase configurado'}\n"
        f"Pares: {', '.join(formatear_par(p) for p in PARES)}\n"
        f"Capital de la sesión: ${capital_sesion:.2f} {MONEDA_CAPITAL}\n"
        f"Take Profit: {PROFIT_TARGET * 100:.0f}% | Stop Loss: {STOP_LOSS * 100:.0f}%"
    )

    hilo_resumen = threading.Thread(target=hilo_resumen_periodico, args=(posiciones,), daemon=True)
    hilo_resumen.start()
    logger.info(f"Hilo de resumen periodico iniciado (cada {INTERVALO_RESUMEN_SEGUNDOS // 60} minutos).")

    try:
        while True:
            try:
                actualizar_heartbeat_supabase(supabase)
                main_analysis(posiciones, supabase, api_key, api_secret, capital_por_par)
            except Exception as error:
                logger.exception(f"Error critico en el ciclo principal: {error}")
                enviar_telegram(f"❌ Error critico en el bot de Kraken: {error}")

            logger.info(f"Esperando {INTERVALO_ANALISIS_SEGUNDOS} segundos para el proximo analisis...")
            time.sleep(INTERVALO_ANALISIS_SEGUNDOS)
    except KeyboardInterrupt:
        logger.info("Señal de interrupción recibida (Ctrl+C). Cerrando...")
        intentar_cerrar_sesion(supabase, session_id, posiciones, api_key, api_secret)
        logger.info("Bot detenido.")


if __name__ == "__main__":
    main()
