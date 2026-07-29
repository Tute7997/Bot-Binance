"""
Configuracion y parametros de estrategia para el bot multi-par de Kraken
(kraken-bot-multi.py). Sin secretos ni I/O de red aca: solo constantes.
"""

import os

from dotenv import load_dotenv

load_dotenv(dotenv_path=".env.local")

# =============================================================================
# PARES Y CAPITAL
# =============================================================================

# Nota: a diferencia de BTC/ETH, Kraken NO usa prefijo X/Z para SOL.
PARES = ["XXBTZUSD", "XETHZUSD", "SOLUSD"]
NOMBRES_PARES = {"XXBTZUSD": "BTC/USD", "XETHZUSD": "ETH/USD", "SOLUSD": "SOL/USD"}
MONEDA_CAPITAL = "USD"
ACTIVO_KRAKEN = "ZUSD"

# Capital minimo (y de arranque) por par. Se compone con la ganancia
# acumulada de sesiones cerradas de ese mismo par (ver supabase_manager.
# obtener_capital_por_par), nunca baja de este piso.
CAPITAL_FLOOR_USD = 5.0

# =============================================================================
# MODO SIMULACION / DEBUG
# =============================================================================

# Variable DISTINTA de KRAKEN_MODO_SIMULACION (la del bot viejo): ambos bots
# comparten el mismo .env.local, asi que necesitan toggles independientes.
MODO_SIMULACION = os.getenv("KRAKEN_MULTI_MODO_SIMULACION", "true").strip().lower() == "true"

# Reutilizada del bot viejo: pura verbosidad de logging, sin efecto financiero.
DEBUG_AUTH = os.getenv("KRAKEN_DEBUG_AUTH", "false").strip().lower() == "true"

# =============================================================================
# TAKE PROFIT / STOP LOSS / TIMEOUT
# =============================================================================

PROFIT_TARGET = 0.05  # +5%
STOP_LOSS = 0.03  # -3%

# Si pasan estas horas sin tocar TP ni SL, se cierra a mercado igual
# (break-even aproximado), para no dejar capital inmovilizado indefinidamente.
TIMEOUT_HORAS = 3
TIMEOUT_SEGUNDOS = TIMEOUT_HORAS * 3600

# =============================================================================
# INDICADORES: RSI, MACD (identico al bot viejo / Bot Binance) + ATR (nuevo)
# =============================================================================

RSI_PERIODO = 14
RSI_UMBRAL = 30
MACD_RAPIDA = 12
MACD_LENTA = 26
MACD_SENAL = 9

# Filtro de volatilidad nuevo: ATR% (ATR / precio de cierre) minimo para
# habilitar una entrada, ademas de la señal RSI+MACD. Se loguea siempre
# (pase o no el filtro) para poder calibrarlo con datos reales.
ATR_PERIODO = 14
UMBRAL_VOLATILIDAD_PCT = 0.0015  # 0.15% del precio, punto de partida

# =============================================================================
# VELAS Y FRECUENCIA DE ANALISIS
# =============================================================================

INTERVALO_VELA_MINUTOS = 1
INTERVALO_ANALISIS_SEGUNDOS = 60

# =============================================================================
# KRAKEN API
# =============================================================================

KRAKEN_BASE_URL = "https://api.kraken.com"

# =============================================================================
# ARCHIVOS DE LOG (sin JSON de posiciones: el estado se reconstruye desde
# Supabase al arrancar, ver supabase_manager.obtener_posiciones_abiertas)
# =============================================================================

ARCHIVO_LOG_BOT = "kraken_bot_multi.log"
ARCHIVO_LOG_OPERACIONES = "operaciones_kraken_multi.csv"

# =============================================================================
# TABLAS DE SUPABASE (nuevas, no colisionan con las del bot viejo)
# =============================================================================

TABLA_SESSIONS_MULTI = "sessions_multi"
TABLA_TRADES_MULTI = "trades_multi"
