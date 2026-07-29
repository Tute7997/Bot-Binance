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


def crear_sesion(supabase, pair: str, capital_usado: float):
    """Inserta una fila 'abierta' en sessions_multi para este par. Devuelve el id o None."""
    if supabase is None:
        return None

    try:
        respuesta = supabase.table(config.TABLA_SESSIONS_MULTI).insert({
            "pair": pair,
            "capital_usado": capital_usado,
            "estado": "abierta",
        }).execute()
        session_id = respuesta.data[0]["id"]
        logger.info(f"[{pair}] Sesion multi creada (id={session_id}, capital_usado={capital_usado:.2f}).")
        return session_id
    except Exception as error:
        logger.error(f"[{pair}] Error creando sesion en sessions_multi: {error}")
        return None


def cerrar_sesion(supabase, session_id, ganancia: float, razon_cierre: str) -> None:
    """Cierra una fila de sessions_multi con el resultado final. capital_usado no cambia: se fija al crear la sesion."""
    if supabase is None or session_id is None:
        return

    try:
        supabase.table(config.TABLA_SESSIONS_MULTI).update({
            "timestamp_cierre": datetime.now(timezone.utc).isoformat(),
            "ganancia": ganancia,
            "estado": "cerrada",
            "razon_cierre": razon_cierre,
        }).eq("id", session_id).execute()
        logger.info(f"Sesion multi {session_id} cerrada (ganancia={ganancia:+.2f}, razon={razon_cierre}).")
    except Exception as error:
        logger.error(f"Error cerrando sesion multi {session_id}: {error}")


def crear_trade(supabase, session_id, pair: str, precio_entrada: float, cantidad: float):
    """Inserta una fila en trades_multi (sin 'estado': se detecta abierta via precio_salida NULL). Devuelve el id o None."""
    if supabase is None:
        return None

    try:
        respuesta = supabase.table(config.TABLA_TRADES_MULTI).insert({
            "session_id": session_id,
            "pair": pair,
            "precio_entrada": precio_entrada,
            "cantidad": cantidad,
            "timestamp_entrada": datetime.now(timezone.utc).isoformat(),
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
            "precio_salida": salida,
            "porcentaje_ganancia": porcentaje,
            "tipo_salida": motivo,
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
    Lee sessions_multi (estado='abierta') y, para cada una, el trade sin
    cerrar (precio_salida NULL) en trades_multi -- esta tabla no tiene
    columna 'estado', asi que "abierto" se define estructuralmente asi.
    Devuelve {pair: {session_id, trade_id, precio_entrada, cantidad, ...}}
    """
    if supabase is None:
        return {}

    try:
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
        capital_usado = float(sesion.get("capital_usado") or config.CAPITAL_FLOOR_USD)

        try:
            trades = (
                supabase.table(config.TABLA_TRADES_MULTI)
                .select("*")
                .eq("session_id", session_id)
                .is_("precio_salida", "null")
                .execute()
            )

            if not trades.data:
                logger.warning(
                    f"[{pair}] Sesion {session_id} esta 'abierta' pero no tiene trade sin cerrar; se ignora."
                )
                continue

            trade = trades.data[0]  # deberia haber como mucho un trade abierto por sesion
            timestamp_entrada = trade.get("timestamp_entrada")

            posiciones[pair] = {
                "session_id": session_id,
                "trade_id": trade["id"],
                "precio_entrada": float(trade["precio_entrada"]),
                "cantidad": float(trade["cantidad"]),
                "capital_usado": capital_usado,
                "timestamp_entrada": (
                    datetime.fromisoformat(timestamp_entrada.replace("Z", "+00:00"))
                    if timestamp_entrada else datetime.now(timezone.utc)
                ),
            }
            logger.info(f"[{pair}] Posicion abierta reconstruida (trade_id={trade['id']}).")

        except Exception as error:
            logger.error(f"[{pair}] Error leyendo trades de sesion {session_id}: {error}")

    return posiciones
