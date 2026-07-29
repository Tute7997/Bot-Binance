"""
Cliente de Supabase y CRUD de "sessions_multi" / "trades_multi" para el bot
multi-par de Kraken (kraken-bot-multi.py). Mismo proyecto de Supabase que el
resto de los bots, pero tablas nuevas, exclusivas de este bot.

Todas las funciones son defensivas: si Supabase no esta configurado o falla,
devuelven un valor por defecto seguro (None / {} / el piso de capital) en vez
de lanzar una excepcion. El bot tiene que poder seguir corriendo aunque
Supabase este caido.
"""

import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from supabase import create_client

import config

logger = logging.getLogger("kraken_bot_multi")


def obtener_cliente_supabase():
    """Crea el cliente de Supabase (mismo proyecto que Bot Binance / Bot Kraken)."""
    load_dotenv(dotenv_path=".env.local")

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        logger.warning(
            "Faltan SUPABASE_URL o SUPABASE_KEY en .env.local. "
            "El bot sigue corriendo, pero sin persistir sesiones/trades."
        )
        return None

    try:
        cliente = create_client(url, key)
        logger.info("Cliente de Supabase inicializado correctamente.")
        return cliente
    except Exception as error:
        logger.error(f"Error inicializando cliente de Supabase: {error}")
        return None


def crear_sesion(supabase, pair: str, capital_inicial: float):
    """Inserta una fila 'abierta' en sessions_multi para este par. Devuelve el id o None."""
    if supabase is None:
        return None

    try:
        respuesta = supabase.table(config.TABLA_SESSIONS_MULTI).insert({
            "pair": pair,
            "capital_inicial": capital_inicial,
            "estado": "abierta",
        }).execute()
        session_id = respuesta.data[0]["id"]
        logger.info(f"[{pair}] Sesion multi creada (id={session_id}, capital_inicial={capital_inicial:.2f}).")
        return session_id
    except Exception as error:
        logger.error(f"[{pair}] Error creando sesion en sessions_multi: {error}")
        return None


def cerrar_sesion(supabase, session_id, capital_final: float, ganancia: float) -> None:
    """Cierra una fila de sessions_multi con el resultado final."""
    if supabase is None or session_id is None:
        return

    try:
        supabase.table(config.TABLA_SESSIONS_MULTI).update({
            "timestamp_cierre": datetime.now(timezone.utc).isoformat(),
            "capital_final": capital_final,
            "ganancia": ganancia,
            "estado": "cerrada",
        }).eq("id", session_id).execute()
        logger.info(f"Sesion multi {session_id} cerrada (ganancia={ganancia:+.2f}).")
    except Exception as error:
        logger.error(f"Error cerrando sesion multi {session_id}: {error}")


def crear_trade(supabase, session_id, pair: str, entrada: float, cantidad: float, simulado: bool, orden_id: str | None):
    """Inserta una fila 'abierta' en trades_multi. Devuelve el id o None."""
    if supabase is None:
        return None

    try:
        respuesta = supabase.table(config.TABLA_TRADES_MULTI).insert({
            "session_id": session_id,
            "pair": pair,
            "side": "BUY",
            "entrada": entrada,
            "cantidad": cantidad,
            "estado": "abierta",
            "simulado": simulado,
            "orden_id_kraken": orden_id,
        }).execute()
        trade_id = respuesta.data[0]["id"]
        logger.info(f"[{pair}] Trade multi creado (id={trade_id}, session_id={session_id}).")
        return trade_id
    except Exception as error:
        logger.error(f"[{pair}] Error creando trade en trades_multi: {error}")
        return None


def cerrar_trade(supabase, trade_id, salida: float, porcentaje: float, motivo: str) -> None:
    """Cierra una fila de trades_multi con el precio de salida y el motivo."""
    if supabase is None or trade_id is None:
        return

    try:
        supabase.table(config.TABLA_TRADES_MULTI).update({
            "salida": salida,
            "porcentaje": porcentaje,
            "motivo_cierre": motivo,
            "estado": "cerrada",
            "timestamp_salida": datetime.now(timezone.utc).isoformat(),
        }).eq("id", trade_id).execute()
        logger.info(f"Trade multi {trade_id} cerrado (motivo={motivo}, porcentaje={porcentaje:+.2f}%).")
    except Exception as error:
        logger.error(f"Error cerrando trade multi {trade_id}: {error}")


def obtener_capital_por_par(supabase, pair: str, floor: float = config.CAPITAL_FLOOR_USD) -> float:
    """
    Capital compuesto para el proximo trade de este par: piso + suma de
    ganancias de sus sesiones cerradas. Si Supabase no esta disponible o
    falla la consulta, devuelve el piso (arranca "de cero" para ese par).
    """
    if supabase is None:
        return floor

    try:
        respuesta = (
            supabase.table(config.TABLA_SESSIONS_MULTI)
            .select("ganancia")
            .eq("pair", pair)
            .eq("estado", "cerrada")
            .execute()
        )
        suma_ganancias = sum(float(fila["ganancia"] or 0) for fila in respuesta.data)
        capital = max(floor, floor + suma_ganancias)
        logger.info(f"[{pair}] Capital compuesto calculado: {capital:.2f} (suma ganancias cerradas: {suma_ganancias:+.2f}).")
        return capital
    except Exception as error:
        logger.error(f"[{pair}] Error calculando capital compuesto, se usa el piso ({floor}): {error}")
        return floor


def obtener_posiciones_abiertas(supabase) -> dict:
    """
    Reconstruye el estado de posiciones abiertas al arrancar.
    Lee sessions_multi (estado='abierta') y obtiene sus trades asociados.
    Devuelve {pair: {session_id, trade_id, precio_entrada, cantidad, ...}}
    """
    if supabase is None:
        return {}
    
    try:
        # PRIMERO: Traer sesiones ABIERTAS
        sesiones = (
            supabase.table(config.TABLA_SESSIONS_MULTI)
            .select("*")
            .eq("estado", "abierta")
            .execute()
        )
    except Exception as error:
        logger.error(f"Error leyendo sesiones abiertas: {error}")
        return {}
    
    posiciones = {}
    
    for sesion in sesiones.data:
        session_id = sesion["id"]
        pair = sesion["pair"]
        capital_inicial = float(sesion.get("capital_usado", config.CAPITAL_FLOOR_USD))
        
        try:
            # SEGUNDO: Traer trades de esa sesión
            trades = (
                supabase.table(config.TABLA_TRADES_MULTI)
                .select("*")
                .eq("session_id", session_id)
                .execute()
            )
            
            if trades.data:
                # Tomar el último trade (el más reciente, que está abierto)
                trade = trades.data[-1]
                timestamp_entrada = trade.get("timestamp_entrada")
                
                posiciones[pair] = {
                    "session_id": session_id,
                    "trade_id": trade["id"],
                    "precio_entrada": float(trade["precio_entrada"]),
                    "cantidad": float(trade["cantidad"]),
                    "capital_usado": capital_inicial,
                    "timestamp_entrada": (
                        datetime.fromisoformat(timestamp_entrada.replace("Z", "+00:00"))
                        if timestamp_entrada else datetime.now(timezone.utc)
                    ),
                }
                logger.info(f"[{pair}] Posicion abierta reconstruida (trade_id={trade['id']}).")
        
        except Exception as error:
            logger.error(f"[{pair}] Error leyendo trades de sesion {session_id}: {error}")
    
    return posiciones
