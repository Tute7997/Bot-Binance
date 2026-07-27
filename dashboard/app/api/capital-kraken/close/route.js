import { NextResponse } from "next/server";
import { obtenerBalanceKraken } from "../../../../lib/krakenSigned";
import { obtenerClienteSupabase } from "../../../../lib/supabaseClient";

// Cierra la sesion activa: calcula el profit contra el balance real de
// Kraken y lo acumula en account_state.capital_depositado. Se niega a
// cerrar si hay alguna posicion con status='open' en trades_kraken, para
// no perder el seguimiento del profit real de esa posicion.
export async function POST() {
  const apiKey = process.env.KRAKEN_API_KEY;
  const apiSecret = process.env.KRAKEN_PRIVATE_KEY;

  if (!apiKey || !apiSecret) {
    return NextResponse.json(
      { error: "Faltan KRAKEN_API_KEY / KRAKEN_PRIVATE_KEY en dashboard/.env.local." },
      { status: 500 }
    );
  }

  try {
    const supabase = obtenerClienteSupabase();

    const { data: estado, error: errorEstado } = await supabase
      .from("account_state")
      .select("*")
      .eq("id", 1)
      .maybeSingle();
    if (errorEstado) throw errorEstado;

    const sessionId = estado?.session_activa;
    if (!sessionId) {
      return NextResponse.json({ error: "No hay ninguna sesión activa para cerrar." }, { status: 400 });
    }

    const { data: posicionesAbiertas, error: errorPosiciones } = await supabase
      .from("trades_kraken")
      .select("id")
      .eq("status", "open")
      .limit(1);
    if (errorPosiciones) throw errorPosiciones;

    if ((posicionesAbiertas || []).length > 0) {
      return NextResponse.json(
        { error: "Hay una posición abierta. Esperá a que se cierre por TP/SL antes de cerrar la sesión." },
        { status: 400 }
      );
    }

    const { data: sesion, error: errorSesion } = await supabase
      .from("sessions")
      .select("capital_inicial")
      .eq("session_id", sessionId)
      .maybeSingle();
    if (errorSesion) throw errorSesion;
    if (!sesion) throw new Error("No se encontró la sesión activa en la tabla 'sessions'.");

    const capitalInicial = parseFloat(sesion.capital_inicial || 0);
    const capitalFinal = await obtenerBalanceKraken({ apiKey, apiSecret, activo: "ZUSD" });
    const profit = capitalFinal - capitalInicial;
    const profitPercent = capitalInicial > 0 ? (profit / capitalInicial) * 100 : 0;

    const { error: errorUpdateSesion } = await supabase
      .from("sessions")
      .update({
        end_time: new Date().toISOString(),
        capital_final: capitalFinal,
        profit,
        profit_percent: profitPercent,
        status: "closed",
      })
      .eq("session_id", sessionId);
    if (errorUpdateSesion) throw errorUpdateSesion;

    const capitalDepositadoActual = parseFloat(estado?.capital_depositado || 0);

    const { error: errorUpdateEstado } = await supabase
      .from("account_state")
      .update({
        capital_depositado: capitalDepositadoActual + profit,
        capital_operando: 0,
        session_activa: null,
        updated_at: new Date().toISOString(),
      })
      .eq("id", 1);
    if (errorUpdateEstado) throw errorUpdateEstado;

    return NextResponse.json({
      sessionId,
      capitalInicial,
      capitalFinal,
      profit,
      profitPercent,
      mensaje:
        "Sesión cerrada. Si kraken-bot.py sigue corriendo, reinicialo (Ctrl+C) para que deje de operar en esta sesión.",
    });
  } catch (error) {
    return NextResponse.json({ error: `No se pudo cerrar la sesión: ${error.message}` }, { status: 500 });
  }
}
