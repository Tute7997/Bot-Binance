import { NextResponse } from "next/server";
import { obtenerBalanceKraken } from "../../../lib/krakenSigned";
import { obtenerClienteSupabase } from "../../../lib/supabaseClient";

const HEARTBEAT_LIMITE_SEGUNDOS = 150;
const HISTORICO_LIMITE = 10;

async function obtenerEstadoBot(supabase) {
  try {
    const { data: heartbeat, error } = await supabase
      .from("kraken_heartbeat")
      .select("last_check")
      .eq("id", 1)
      .maybeSingle();

    if (error || !heartbeat?.last_check) return "desconocido";

    const segundosDesdeUltimoCheck = (Date.now() - new Date(heartbeat.last_check).getTime()) / 1000;
    return segundosDesdeUltimoCheck <= HEARTBEAT_LIMITE_SEGUNDOS ? "operando" : "inactivo";
  } catch {
    return "desconocido";
  }
}

export async function GET() {
  const apiKey = process.env.KRAKEN_API_KEY;
  const apiSecret = process.env.KRAKEN_PRIVATE_KEY;
  // Kraken no tiene pares en ARS (verificado en vivo contra /0/public/AssetPairs):
  // XXBTZUSD/XETHZUSD cotizan en USD, asi que el balance se lee en USD (ZUSD).

  if (!apiKey || !apiSecret) {
    return NextResponse.json({
      configurado: false,
      mensaje: "Faltan KRAKEN_API_KEY / KRAKEN_PRIVATE_KEY en dashboard/.env.local.",
    });
  }

  try {
    const supabase = obtenerClienteSupabase();

    const [estadoRespuesta, balanceActual, estadoBot, posicionesAbiertasRespuesta, historicoRespuesta] =
      await Promise.all([
        supabase.from("account_state").select("*").eq("id", 1).maybeSingle(),
        obtenerBalanceKraken({ apiKey, apiSecret, activo: "ZUSD" }),
        obtenerEstadoBot(supabase),
        supabase.from("trades_kraken").select("id").eq("status", "open").limit(1),
        supabase
          .from("sessions")
          .select("*")
          .eq("status", "closed")
          .order("end_time", { ascending: false })
          .limit(HISTORICO_LIMITE),
      ]);

    if (estadoRespuesta.error) throw estadoRespuesta.error;

    const cuenta = estadoRespuesta.data || { capital_depositado: 0, capital_operando: 0, session_activa: null };
    const hayPosicionAbierta = (posicionesAbiertasRespuesta.data || []).length > 0;

    const historicoSesiones = (historicoRespuesta.data || []).map((sesion) => ({
      sessionId: sesion.session_id,
      startTime: sesion.start_time,
      endTime: sesion.end_time,
      capitalInicial: parseFloat(sesion.capital_inicial || 0),
      capitalFinal: parseFloat(sesion.capital_final || 0),
      profit: parseFloat(sesion.profit || 0),
      profitPercent: parseFloat(sesion.profit_percent || 0),
    }));

    let sesionActiva = null;
    if (cuenta.session_activa) {
      const { data: sesion, error: errorSesion } = await supabase
        .from("sessions")
        .select("*")
        .eq("session_id", cuenta.session_activa)
        .maybeSingle();

      if (!errorSesion && sesion) {
        const capitalInicial = parseFloat(sesion.capital_inicial || 0);
        const profit = balanceActual - capitalInicial;
        const profitPercent = capitalInicial > 0 ? (profit / capitalInicial) * 100 : 0;

        sesionActiva = {
          sessionId: sesion.session_id,
          startTime: sesion.start_time,
          capitalInicial,
          profit,
          profitPercent,
        };
      }
    }

    return NextResponse.json({
      configurado: true,
      capitalDepositado: parseFloat(cuenta.capital_depositado || 0),
      capitalOperando: parseFloat(cuenta.capital_operando || 0),
      balanceActual,
      moneda: "USD",
      sesionActiva,
      hayPosicionAbierta,
      estadoBot,
      historicoSesiones,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return NextResponse.json(
      { configurado: true, error: `No se pudo leer el estado de Kraken: ${error.message}` },
      { status: 500 }
    );
  }
}
