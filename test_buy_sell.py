"""
Script de testing standalone para verificar que la integracion Supabase +
Telegram del bot funciona de punta a punta, sin esperar a que el bot real
(main.py) dispare una senal de compra.

Simula una compra y una venta de 0.001 BTC (usando el precio real de mercado
de Binance Testnet como referencia), pero NO ejecuta ninguna orden real en
Binance: solo escribe el trade en Supabase y manda las alertas de Telegram,
que es lo que este test necesita verificar. No importa ni modifica main.py.
"""

import os
import random
import time
from datetime import datetime

import requests
from binance.client import Client
from binance.exceptions import BinanceAPIException
from dotenv import load_dotenv
from supabase import create_client

# =============================================================================
# CONFIGURACION DEL TEST
# =============================================================================

PAR_DE_PRUEBA = "BTCUSDT"
CANTIDAD_DE_PRUEBA = 0.001
SEGUNDOS_DE_ESPERA = 10  # tiempo simulado entre "compra" y "venta"
BINANCE_TESTNET_URL = "https://testnet.binance.vision/api"
TABLA_TRADES = "trades"


# =============================================================================
# FUNCIONES COPIADAS DE main.py (sin modificaciones en su comportamiento)
# =============================================================================

def cargar_cliente_binance():
    """Crea el cliente de Binance Testnet a partir de las credenciales en .env.local."""
    load_dotenv(dotenv_path=".env.local")

    api_key = os.getenv("BINANCE_API_KEY")
    api_secret = os.getenv("BINANCE_API_SECRET")

    if not api_key or not api_secret:
        raise ValueError("Faltan BINANCE_API_KEY o BINANCE_API_SECRET en .env.local")

    cliente = Client(api_key, api_secret, testnet=True)
    cliente.API_URL = BINANCE_TESTNET_URL
    return cliente


def cargar_cliente_supabase():
    """Crea el cliente de Supabase a partir de las credenciales en .env.local."""
    load_dotenv(dotenv_path=".env.local")

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")

    if not url or not key:
        raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY en .env.local")

    return create_client(url, key)


def cargar_config_telegram():
    """Obtiene el token del bot y el chat id de Telegram desde .env.local."""
    load_dotenv(dotenv_path=".env.local")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("⚠️  Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en .env.local. No se enviaran alertas.")

    return token, chat_id


TELEGRAM_TOKEN, TELEGRAM_CHAT_ID = cargar_config_telegram()


def enviar_telegram(mensaje: str) -> None:
    """Envia un mensaje de alerta al chat de Telegram configurado."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}

    try:
        respuesta = requests.post(url, data=payload, timeout=10)
        respuesta.raise_for_status()
        print(f"📨 Telegram enviado: {mensaje.splitlines()[0]}")
    except requests.RequestException as error:
        print(f"❌ Error enviando mensaje a Telegram: {error}")


def get_balance(cliente: Client, activo: str = "USDT") -> float:
    """Obtiene el saldo disponible (libre) de un activo en la cuenta de Testnet."""
    try:
        cuenta = cliente.get_asset_balance(asset=activo)
        return float(cuenta["free"]) if cuenta else 0.0
    except BinanceAPIException as error:
        print(f"❌ Error obteniendo balance de {activo}: {error}")
        return 0.0


def registrar_trade_apertura_supabase(supabase, par: str, precio_entrada: float, cantidad: float):
    """Inserta una fila en la tabla 'trades' de Supabase al abrir una posicion."""
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
        print(f"💾 Trade insertado en Supabase (id={trade_id}).")
        return trade_id
    except Exception as error:
        print(f"❌ Error insertando trade en Supabase: {error}")
        return None


def actualizar_trade_cierre_supabase(
    supabase, trade_id, precio_salida: float, ganancia_usd: float, ganancia_pct: float, motivo: str
) -> None:
    """Actualiza en Supabase la fila del trade que se acaba de cerrar."""
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
        print(f"💾 Trade {trade_id} actualizado en Supabase (cierre {motivo}).")
    except Exception as error:
        print(f"❌ Error actualizando trade {trade_id} en Supabase: {error}")


# =============================================================================
# FLUJO DEL TEST
# =============================================================================

def main() -> None:
    print("=" * 60)
    print("TEST DE COMPRA/VENTA - Verificacion Supabase + Telegram")
    print("=" * 60)

    try:
        cliente = cargar_cliente_binance()
        print("✅ Conectado a Binance Testnet.")
    except ValueError as error:
        print(f"❌ No se pudo conectar a Binance: {error}")
        return

    try:
        supabase = cargar_cliente_supabase()
        print("✅ Conectado a Supabase.")
    except ValueError as error:
        print(f"❌ No se pudo conectar a Supabase: {error}")
        return

    balance = get_balance(cliente, "USDT")
    print(f"💰 Balance actual disponible: {balance:.2f} USDT")

    # --- Paso 1: SIMULAR COMPRA ---
    try:
        precio_entrada = float(cliente.get_symbol_ticker(symbol=PAR_DE_PRUEBA)["price"])
    except BinanceAPIException as error:
        print(f"❌ Error obteniendo precio de mercado: {error}")
        return

    print(f"\n📈 Precio actual de {PAR_DE_PRUEBA}: ${precio_entrada:.2f}")
    print(f"🟢 SIMULANDO COMPRA: {CANTIDAD_DE_PRUEBA} {PAR_DE_PRUEBA} a ${precio_entrada:.2f}")

    trade_id = registrar_trade_apertura_supabase(
        supabase, PAR_DE_PRUEBA, precio_entrada, CANTIDAD_DE_PRUEBA
    )

    if trade_id is None:
        print("❌ No se pudo registrar el trade en Supabase. Se aborta el test.")
        return

    enviar_telegram(f"🧪 TEST COMPRA: BTC a ${precio_entrada:.2f}")

    print(f"\n⏳ Esperando {SEGUNDOS_DE_ESPERA} segundos antes de simular la venta...")
    time.sleep(SEGUNDOS_DE_ESPERA)

    # --- Paso 2: SIMULAR VENTA (resultado aleatorio: +2% o -1%) ---
    profit_pct = random.choice([0.02, -0.01])
    precio_salida = precio_entrada * (1 + profit_pct)
    ganancia_usd = (precio_salida - precio_entrada) * CANTIDAD_DE_PRUEBA
    motivo = "TP" if profit_pct > 0 else "SL"

    print(f"\n🔴 SIMULANDO VENTA: resultado {'+2%' if profit_pct > 0 else '-1%'} ({motivo})")
    print(f"   Precio salida: ${precio_salida:.2f} | Ganancia: ${ganancia_usd:.4f}")

    actualizar_trade_cierre_supabase(
        supabase, trade_id, precio_salida, ganancia_usd, profit_pct * 100, motivo
    )

    texto_resultado = "Ganancia" if ganancia_usd >= 0 else "Pérdida"
    enviar_telegram(f"🧪 TEST VENTA: {texto_resultado} ${ganancia_usd:.4f} ({motivo})")

    # --- Paso 3: verificar que quedo bien guardado en Supabase ---
    print("\n🔍 Verificando el trade guardado en Supabase...")
    try:
        respuesta = supabase.table(TABLA_TRADES).select("*").eq("id", trade_id).execute()
        fila = respuesta.data[0] if respuesta.data else None
    except Exception as error:
        print(f"❌ Error verificando el trade en Supabase: {error}")
        fila = None

    if fila:
        print("   Fila en Supabase:")
        for clave, valor in fila.items():
            print(f"     {clave}: {valor}")
        verificacion_ok = fila.get("status") == "closed" and fila.get("exit_price") is not None
    else:
        verificacion_ok = False

    # --- Resumen final ---
    print("\n" + "=" * 60)
    if verificacion_ok:
        print("✅ TEST COMPLETADO")
    else:
        print("⚠️  TEST TERMINADO CON ADVERTENCIAS (revisar el detalle de arriba)")
    print("=" * 60)
    print(f"- Revisá la tabla 'trades' en Supabase, fila con id = {trade_id}")
    print("- Revisá tu Telegram: deberían haber llegado 2 mensajes (TEST COMPRA y TEST VENTA)")
    print("- Próximos pasos:")
    print("    1) Si todo se ve bien, main.py ya puede registrar trades reales igual que este test.")
    print(f"    2) (Opcional) borrá la fila id={trade_id} en Supabase si no querés que aparezca")
    print("       mezclada con trades reales en el dashboard.")
    print(f"    3) Corré el dashboard (cd dashboard && npm run dev) y confirmá que este trade aparece.")


if __name__ == "__main__":
    main()
