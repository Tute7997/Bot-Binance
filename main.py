"""
Bot de trading automatico para Binance Testnet.
Analiza BTC/USDT y ETH/USDT cada minuto usando RSI + MACD y opera con dinero
ficticio de Binance Testnet. Envia alertas a Telegram y deja registro de
cada operacion en logs y en un CSV.
"""

import csv
import json
import logging
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException
from dotenv import load_dotenv
from supabase import create_client

# =============================================================================
# CONFIGURACION Y PARAMETROS DE LA ESTRATEGIA
# =============================================================================

# Pares que el bot va a analizar y operar
PARES = ["BTCUSDT", "ETHUSDT"]

# Capital total asignado a la estrategia y reparto en partes iguales por par
CAPITAL_TOTAL_USD = 500
CAPITAL_POR_PAR = {par: CAPITAL_TOTAL_USD / len(PARES) for par in PARES}

# Objetivo de ganancia (take profit) y limite de perdida (stop loss)
PROFIT_TARGET = 0.05  # +5%
STOP_LOSS = 0.03  # -3%

# Parametros de los indicadores tecnicos
RSI_PERIODO = 14
RSI_UMBRAL = 30
MACD_RAPIDA = 12
MACD_LENTA = 26
MACD_SENAL = 9

# Configuracion de velas y frecuencia de analisis
INTERVALO_VELA = Client.KLINE_INTERVAL_1MINUTE
VELAS_A_SOLICITAR = 100  # historial suficiente para que MACD/RSI sean estables
INTERVALO_ANALISIS_SEGUNDOS = 60

# Archivos de estado y de registro
ARCHIVO_POSICIONES = "posiciones.json"
ARCHIVO_LOG_OPERACIONES = "operaciones.csv"
ARCHIVO_LOG_BOT = "bot.log"

# Endpoint oficial de Binance Spot Testnet (resguardo por compatibilidad de version)
BINANCE_TESTNET_URL = "https://testnet.binance.vision/api"

# Nombres de tablas en Supabase (usadas por el dashboard)
TABLA_TRADES = "trades"
TABLA_HEARTBEAT = "bot_heartbeat"
HEARTBEAT_ID = 1  # fila unica que se va actualizando (upsert) en cada ciclo


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
logger = logging.getLogger("bot_binance")


# =============================================================================
# CARGA DE CONFIGURACION / CLIENTE DE BINANCE
# =============================================================================

def cargar_cliente_binance():
    """
    Carga las credenciales desde .env.local y crea el cliente de Binance
    apuntando a Binance Testnet (dinero ficticio, sin riesgo real).
    """
    load_dotenv(dotenv_path=".env.local")

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError(
            "Faltan BINANCE_API_KEY o BINANCE_API_SECRET en .env.local"
        )

    cliente = Client(api_key, api_secret, testnet=True)
    # Se fuerza la URL oficial de testnet como resguardo, por si la version
    # instalada de python-binance no resuelve testnet=True correctamente.
    cliente.API_URL = BINANCE_TESTNET_URL

    logger.info("Cliente de Binance Testnet inicializado correctamente.")
    return cliente


def cargar_cliente_supabase():
    """
    Carga las credenciales desde .env.local y crea el cliente de Supabase,
    usado unicamente para que el dashboard web pueda leer el estado del bot
    y el historial de trades. Si falta la configuracion, el bot sigue
    funcionando igual (solo no se refleja en el dashboard).
    """
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

def enviar_telegram(mensaje: str) -> None:
    """
    Envia un mensaje de alerta al chat de Telegram configurado.
    No debe nunca tumbar el bot si Telegram falla: solo se registra el error.
    """
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
# OBTENCION DE DATOS HISTORICOS (VELAS)
# =============================================================================

def get_klines(cliente: Client, simbolo: str) -> pd.DataFrame:
    """
    Obtiene las velas (klines) de 1 minuto para un simbolo y arma un
    DataFrame con columnas OHLCV. Se descarta la ultima vela porque
    todavia esta en formacion (no cerrada), para que los indicadores
    se calculen solo sobre datos confirmados.
    """
    velas = cliente.get_klines(
        symbol=simbolo, interval=INTERVALO_VELA, limit=VELAS_A_SOLICITAR
    )

    columnas = [
        "tiempo_apertura", "apertura", "maximo", "minimo", "cierre", "volumen",
        "tiempo_cierre", "volumen_cotizado", "num_operaciones",
        "volumen_compra_activo", "volumen_compra_cotizado", "ignorar",
    ]
    df = pd.DataFrame(velas, columns=columnas)

    # Convertir columnas numericas de string a float
    for col in ["apertura", "maximo", "minimo", "cierre", "volumen"]:
        df[col] = df[col].astype(float)

    df["tiempo_apertura"] = pd.to_datetime(df["tiempo_apertura"], unit="ms")

    # Se descarta la ultima vela (en formacion, aun no cerrada)
    df = df.iloc[:-1].reset_index(drop=True)

    logger.info(f"[{simbolo}] Se obtuvieron {len(df)} velas cerradas de 1m.")
    return df


# =============================================================================
# CALCULO DE INDICADORES: RSI Y MACD
# =============================================================================

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula el RSI (suavizado de Wilder, periodo 14) y el MACD
    (EMA 12/26 + linea de senal EMA 9) sobre la columna de precios de cierre.
    Agrega las columnas resultantes al DataFrame recibido.
    """
    df = df.copy()
    cierre = df["cierre"]

    # --- RSI (Relative Strength Index) ---
    delta = cierre.diff()
    ganancia = delta.clip(lower=0)
    perdida = -delta.clip(upper=0)

    # Suavizado de Wilder (equivalente a una media movil exponencial con alpha=1/periodo)
    media_ganancia = ganancia.ewm(alpha=1 / RSI_PERIODO, min_periods=RSI_PERIODO, adjust=False).mean()
    media_perdida = perdida.ewm(alpha=1 / RSI_PERIODO, min_periods=RSI_PERIODO, adjust=False).mean()

    fuerza_relativa = media_ganancia / media_perdida
    df["rsi"] = 100 - (100 / (1 + fuerza_relativa))
    # Si no hubo ninguna perdida, el RSI es 100 (evita division por cero -> NaN)
    df["rsi"] = df["rsi"].where(media_perdida != 0, 100)

    # --- MACD (Moving Average Convergence Divergence) ---
    ema_rapida = cierre.ewm(span=MACD_RAPIDA, adjust=False).mean()
    ema_lenta = cierre.ewm(span=MACD_LENTA, adjust=False).mean()
    df["macd"] = ema_rapida - ema_lenta
    df["macd_senal"] = df["macd"].ewm(span=MACD_SENAL, adjust=False).mean()
    df["macd_histograma"] = df["macd"] - df["macd_senal"]

    return df


# =============================================================================
# LOGICA DE SENAL DE COMPRA
# =============================================================================

def buy_signal(df: pd.DataFrame) -> bool:
    """
    Determina si hay senal de compra sobre la ultima vela cerrada:
    - RSI < RSI_UMBRAL (sobreventa)
    - Y el MACD cruzo al alza (linea MACD paso de estar <= senal a estar > senal)
    """
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
# BALANCE Y CALCULO DE CANTIDAD A OPERAR
# =============================================================================

def get_balance(cliente: Client, activo: str = "USDT") -> float:
    """Obtiene el saldo disponible (libre) de un activo en la cuenta de Testnet."""
    try:
        cuenta = cliente.get_asset_balance(asset=activo)
        return float(cuenta["free"]) if cuenta else 0.0
    except BinanceAPIException as error:
        logger.error(f"Error obteniendo balance de {activo}: {error}")
        return 0.0


def obtener_filtro_cantidad(cliente: Client, simbolo: str) -> tuple:
    """
    Obtiene el stepSize y minQty del filtro LOT_SIZE del simbolo, necesarios
    para redondear la cantidad a comprar/vender a un valor valido en Binance.
    """
    info = cliente.get_symbol_info(simbolo)
    for f in info["filters"]:
        if f["filterType"] == "LOT_SIZE":
            return float(f["stepSize"]), float(f["minQty"])
    return 0.000001, 0.000001


def calcular_cantidad_compra(cliente: Client, simbolo: str, monto_usd: float) -> float:
    """
    Calcula la cantidad del activo base a comprar segun el monto en USD
    disponible, redondeada al stepSize permitido por Binance para ese par.
    """
    precio_actual = float(cliente.get_symbol_ticker(symbol=simbolo)["price"])
    step_size, cantidad_minima = obtener_filtro_cantidad(cliente, simbolo)

    cantidad_bruta = monto_usd / precio_actual

    # Redondear hacia abajo al multiplo de step_size mas cercano
    precision = int(round(-np.log10(step_size)))
    cantidad = np.floor(cantidad_bruta / step_size) * step_size
    cantidad = round(cantidad, precision)

    if cantidad < cantidad_minima:
        logger.warning(
            f"[{simbolo}] Cantidad calculada ({cantidad}) es menor a la minima "
            f"permitida ({cantidad_minima}). No se puede operar con este monto."
        )
        return 0.0

    return cantidad


# =============================================================================
# EJECUCION DE ORDENES
# =============================================================================

def place_order(cliente: Client, simbolo: str, lado: str, cantidad: float):
    """
    Ejecuta una orden de mercado (MARKET) de compra o venta.
    lado: "BUY" o "SELL". Devuelve la respuesta de Binance o None si fallo.
    """
    try:
        if lado == "BUY":
            orden = cliente.order_market_buy(symbol=simbolo, quantity=cantidad)
        elif lado == "SELL":
            orden = cliente.order_market_sell(symbol=simbolo, quantity=cantidad)
        else:
            raise ValueError(f"Lado de orden invalido: {lado}")

        logger.info(f"[{simbolo}] Orden {lado} ejecutada correctamente: {orden['orderId']}")
        return orden

    except (BinanceAPIException, BinanceOrderException) as error:
        logger.error(f"[{simbolo}] Error ejecutando orden {lado}: {error}")
        enviar_telegram(f"⚠️ Error ejecutando orden {lado} en {simbolo}: {error}")
        return None


# =============================================================================
# PERSISTENCIA DE POSICIONES ABIERTAS
# =============================================================================

def cargar_posiciones() -> dict:
    """Carga las posiciones abiertas guardadas en disco (si existen)."""
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
    """Guarda el estado actual de posiciones abiertas en disco."""
    try:
        with open(ARCHIVO_POSICIONES, "w", encoding="utf-8") as archivo:
            json.dump(posiciones, archivo, indent=2, ensure_ascii=False)
    except OSError as error:
        logger.error(f"Error guardando {ARCHIVO_POSICIONES}: {error}")


# =============================================================================
# VERIFICACION DE SALIDA (TAKE PROFIT / STOP LOSS)
# =============================================================================

def verificar_salida(posicion: dict, precio_actual: float):
    """
    Compara el precio actual contra el precio de entrada de la posicion.
    Devuelve "TP" si se alcanzo el take profit, "SL" si se alcanzo el
    stop loss, o None si todavia no corresponde salir.
    """
    precio_entrada = posicion["precio_entrada"]
    variacion = (precio_actual - precio_entrada) / precio_entrada

    if variacion >= PROFIT_TARGET:
        return "TP"
    if variacion <= -STOP_LOSS:
        return "SL"
    return None


# =============================================================================
# REGISTRO DE TRADES EN SUPABASE (para el dashboard web)
# =============================================================================

def registrar_trade_apertura_supabase(supabase, par: str, precio_entrada: float, cantidad: float):
    """
    Inserta una fila en la tabla "trades" de Supabase al abrir una posicion.
    Devuelve el id de la fila insertada (para poder actualizarla al cerrar),
    o None si Supabase no esta configurado o la insercion falla.
    """
    if supabase is None:
        return None

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
        return trade_id
    except Exception as error:
        logger.error(f"[{par}] Error insertando trade en Supabase: {error}")
        return None


def actualizar_trade_cierre_supabase(
    supabase, trade_id, precio_salida: float, ganancia_usd: float, ganancia_pct: float, motivo: str
) -> None:
    """
    Actualiza en Supabase la fila del trade que se acaba de cerrar, con el
    precio de salida, la ganancia y el motivo de cierre (TP o SL).
    """
    if supabase is None or trade_id is None:
        return

    try:
        supabase.table(TABLA_TRADES).update({
            "exit_price": precio_salida,
            "profit": ganancia_usd,
            "profit_percent": ganancia_pct,
            "status": "closed",
            "reason": motivo,
        }).eq("id", trade_id).execute()
        logger.info(f"Trade {trade_id} actualizado en Supabase (cierre {motivo}).")
    except Exception as error:
        logger.error(f"Error actualizando trade {trade_id} en Supabase: {error}")


def actualizar_heartbeat_supabase(supabase) -> None:
    """
    Actualiza (upsert) una unica fila en "bot_heartbeat" con la hora actual.
    El dashboard usa la antiguedad de este valor para mostrar si el bot
    esta Activo o Inactivo.
    """
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
    """Agrega una fila con el detalle de la operacion al archivo operaciones.csv."""
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

def main_analysis(cliente: Client, posiciones: dict, supabase=None) -> None:
    """
    Recorre cada par configurado:
    - Si ya hay una posicion abierta en ese par, verifica si corresponde
      cerrarla por take profit o stop loss.
    - Si no hay posicion abierta, calcula los indicadores y evalua si hay
      senal de compra; de ser asi, abre una posicion (maximo 1 por par).
    """
    for simbolo in PARES:
        logger.info(f"--- Analizando {simbolo} ---")
        try:
            if simbolo in posiciones:
                # Ya hay una posicion abierta: solo se revisa si toca salir
                precio_actual = float(cliente.get_symbol_ticker(symbol=simbolo)["price"])
                posicion = posiciones[simbolo]
                motivo_salida = verificar_salida(posicion, precio_actual)

                if motivo_salida:
                    orden = place_order(cliente, simbolo, "SELL", posicion["cantidad"])
                    if orden:
                        ganancia_pct = (precio_actual - posicion["precio_entrada"]) / posicion["precio_entrada"] * 100
                        ganancia_usd = (precio_actual - posicion["precio_entrada"]) * posicion["cantidad"]
                        texto_motivo = "Take Profit alcanzado" if motivo_salida == "TP" else "Stop Loss alcanzado"

                        registrar_operacion(simbolo, "VENTA", precio_actual, posicion["cantidad"], texto_motivo)
                        actualizar_trade_cierre_supabase(
                            supabase, posicion.get("trade_id_supabase"),
                            precio_actual, ganancia_usd, ganancia_pct, motivo_salida,
                        )
                        enviar_telegram(
                            f"🔴 Posicion cerrada en {simbolo}\n"
                            f"Motivo: {texto_motivo}\n"
                            f"Precio entrada: {posicion['precio_entrada']:.2f}\n"
                            f"Precio salida: {precio_actual:.2f}\n"
                            f"Resultado: {ganancia_pct:+.2f}%"
                        )

                        del posiciones[simbolo]
                        guardar_posiciones(posiciones)
                else:
                    logger.info(
                        f"[{simbolo}] Posicion abierta sin cambios. "
                        f"Precio actual: {precio_actual:.2f}, entrada: {posicion['precio_entrada']:.2f}"
                    )
                continue

            # No hay posicion abierta: se evalua senal de compra
            df = get_klines(cliente, simbolo)
            df = calculate_indicators(df)

            ultima_vela = df.iloc[-1]
            logger.info(
                f"[{simbolo}] Cierre: {ultima_vela['cierre']:.2f} | "
                f"RSI: {ultima_vela['rsi']:.2f} | MACD: {ultima_vela['macd']:.4f} | "
                f"Senal MACD: {ultima_vela['macd_senal']:.4f}"
            )

            if buy_signal(df):
                monto_asignado = CAPITAL_POR_PAR[simbolo]
                balance_disponible = get_balance(cliente, "USDT")
                monto_a_usar = min(monto_asignado, balance_disponible)

                if monto_a_usar <= 0:
                    logger.warning(f"[{simbolo}] Sin balance USDT disponible para comprar.")
                    continue

                cantidad = calcular_cantidad_compra(cliente, simbolo, monto_a_usar)
                if cantidad <= 0:
                    continue

                orden = place_order(cliente, simbolo, "BUY", cantidad)
                if orden:
                    precio_entrada = float(ultima_vela["cierre"])
                    trade_id_supabase = registrar_trade_apertura_supabase(
                        supabase, simbolo, precio_entrada, cantidad
                    )
                    posiciones[simbolo] = {
                        "precio_entrada": precio_entrada,
                        "cantidad": cantidad,
                        "timestamp": datetime.now().isoformat(),
                        "trade_id_supabase": trade_id_supabase,
                    }
                    guardar_posiciones(posiciones)

                    registrar_operacion(simbolo, "COMPRA", precio_entrada, cantidad, "Senal RSI+MACD")
                    enviar_telegram(
                        f"🟢 Nueva compra en {simbolo}\n"
                        f"Precio entrada: {precio_entrada:.2f}\n"
                        f"Cantidad: {cantidad}\n"
                        f"Monto: ${monto_a_usar:.2f}\n"
                        f"RSI: {ultima_vela['rsi']:.2f}"
                    )
            else:
                logger.info(f"[{simbolo}] Sin senal de compra por ahora.")

        except BinanceAPIException as error:
            logger.error(f"[{simbolo}] Error de API de Binance: {error}")
            enviar_telegram(f"⚠️ Error de API de Binance analizando {simbolo}: {error}")
        except Exception as error:
            logger.exception(f"[{simbolo}] Error inesperado durante el analisis: {error}")


# =============================================================================
# PUNTO DE ENTRADA: LOOP PRINCIPAL DEL BOT
# =============================================================================

def main() -> None:
    """
    Punto de entrada del bot. Inicializa el cliente de Binance, recupera
    posiciones previas y corre el analisis en un loop infinito cada
    INTERVALO_ANALISIS_SEGUNDOS, sin que un error puntual tumbe el proceso.
    """
    logger.info("=== Iniciando bot de trading en Binance Testnet ===")

    cliente = cargar_cliente_binance()
    supabase = cargar_cliente_supabase()
    posiciones = cargar_posiciones()

    balance_inicial = get_balance(cliente, "USDT")
    logger.info(f"Balance inicial disponible en Testnet: {balance_inicial:.2f} USDT")

    enviar_telegram(
        "🤖 Bot de trading iniciado en Binance Testnet\n"
        f"Pares: {', '.join(PARES)}\n"
        f"Balance USDT: {balance_inicial:.2f}\n"
        f"Take Profit: {PROFIT_TARGET * 100:.0f}% | Stop Loss: {STOP_LOSS * 100:.0f}%"
    )

    while True:
        try:
            actualizar_heartbeat_supabase(supabase)
            main_analysis(cliente, posiciones, supabase)
        except Exception as error:
            # Cualquier error no controlado en un ciclo no debe tumbar el bot
            logger.exception(f"Error critico en el ciclo principal: {error}")
            enviar_telegram(f"❌ Error critico en el bot: {error}")

        logger.info(f"Esperando {INTERVALO_ANALISIS_SEGUNDOS} segundos para el proximo analisis...")
        time.sleep(INTERVALO_ANALISIS_SEGUNDOS)


if __name__ == "__main__":
    main()
