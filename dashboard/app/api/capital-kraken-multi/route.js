import { NextResponse } from "next/server";
import { obtenerClienteSupabase } from "../../../lib/supabaseClient";

// Mismo capital piso que Bot Kraken/config.py (CAPITAL_FLOOR_USD).
const CAPITAL_PISO_USD = 5.0;
const HISTORICO_LIMITE = 50;
const HEARTBEAT_MULTI_LIMITE_SEGUNDOS = 120;

// Mismos pares/nombres que Bot Kraken/config.py (PARES / NOMBRES_PARES).
// No se importa Python desde acá, se redeclara igual que el resto de las
// constantes ya redeclaradas entre el bot y el dashboard (ver krakenSigned.js).
const PARES = ["XXBTZUSD", "XETHZUSD", "SOLUSD"];
const NOMBRES_PARES = { XXBTZUSD: "BTC/USD", XETHZUSD: "ETH/USD", SOLUSD: "SOL/USD" };

function mapearTrade(trade) {
  if (!trade) return null;
  return {
    tradeId: trade.id,
    precioEntrada: parseFloat(trade.precio_entrada || 0),
    precioSalida: trade.precio_salida !== null ? parseFloat(trade.precio_salida) : null,
    cantidad: parseFloat(trade.cantidad || 0),
    porcentajeGanancia: trade.porcentaje_ganancia !== null ? parseFloat(trade.porcentaje_ganancia) : null,
    timestampEntrada: trade.timestamp_entrada,
    timestampSalida: trade.timestamp_salida,
  };
}

async function obtenerEstadoBotMulti(supabase) {
  try {
    const { data, error } = await supabase
      .from("kraken_multi_heartbeat")
      .select("last_check")
      .eq("id", 1)
      .maybeSingle();

    if (error || !data?.last_check) {
      return { activo: false, ultimoCheck: null };
    }

    const segundos = (Date.now() - new Date(data.last_check).getTime()) / 1000;
    return { activo: segundos <= HEARTBEAT_MULTI_LIMITE_SEGUNDOS, ultimoCheck: data.last_check };
  } catch {
    return { activo: false, ultimoCheck: null };
  }
}

export async function GET() {
  if (!process.env.SUPABASE_URL || !process.env.SUPABASE_ANON_KEY) {
    return NextResponse.json({
      configurado: false,
      mensaje: "Faltan SUPABASE_URL o SUPABASE_ANON_KEY en dashboard/.env.local.",
    });
  }

  try {
    const supabase = obtenerClienteSupabase();

    const [sesionesAbiertasRespuesta, sesionesCerradasRespuesta, estadoBotMulti] = await Promise.all([
      supabase.from("sessions_multi").select("*").eq("estado", "abierta"),
      supabase
        .from("sessions_multi")
        .select("*")
        .eq("estado", "cerrada")
        .order("timestamp_cierre", { ascending: false }),
      obtenerEstadoBotMulti(supabase),
    ]);

    if (sesionesAbiertasRespuesta.error) throw sesionesAbiertasRespuesta.error;
    if (sesionesCerradasRespuesta.error) throw sesionesCerradasRespuesta.error;

    const sesionesAbiertas = sesionesAbiertasRespuesta.data || [];
    const sesionesCerradas = sesionesCerradasRespuesta.data || [];
    const sesionesCerradasParaHistorico = sesionesCerradas.slice(0, HISTORICO_LIMITE);

    const idsTrades = [
      ...sesionesAbiertas.map((s) => s.id),
      ...sesionesCerradasParaHistorico.map((s) => s.id),
    ];

    let tradesPorSesion = new Map();
    if (idsTrades.length > 0) {
      const { data: trades, error: errorTrades } = await supabase
        .from("trades_multi")
        .select("*")
        .in("session_id", idsTrades);
      if (errorTrades) throw errorTrades;

      for (const trade of trades || []) {
        tradesPorSesion.set(trade.session_id, trade);
      }
    }

    // Capital compuesto por par: mismo calculo que
    // Bot Kraken/supabase_manager.py -> obtener_capital_por_par (piso +
    // suma de ganancia de TODAS las sesiones cerradas de ese par, no solo
    // las que entran en el historico recortado).
    const gananciaAcumuladaPorPar = {};
    for (const sesion of sesionesCerradas) {
      const ganancia = parseFloat(sesion.ganancia || 0);
      gananciaAcumuladaPorPar[sesion.pair] = (gananciaAcumuladaPorPar[sesion.pair] || 0) + ganancia;
    }

    const sesionAbiertaPorPar = {};
    for (const sesion of sesionesAbiertas) {
      sesionAbiertaPorPar[sesion.pair] = sesion;
    }

    const pares = PARES.map((pair) => {
      const capitalActual = Math.max(CAPITAL_PISO_USD, CAPITAL_PISO_USD + (gananciaAcumuladaPorPar[pair] || 0));
      const sesion = sesionAbiertaPorPar[pair];
      let posicionAbierta = null;

      if (sesion) {
        const trade = tradesPorSesion.get(sesion.id);
        posicionAbierta = {
          sessionId: sesion.id,
          tradeId: trade?.id ?? null,
          precioEntrada: trade ? parseFloat(trade.precio_entrada || 0) : null,
          cantidad: trade ? parseFloat(trade.cantidad || 0) : null,
          capitalUsado: parseFloat(sesion.capital_usado || 0),
          timestampEntrada: trade?.timestamp_entrada ?? sesion.timestamp_inicio,
        };
      }

      return {
        pair,
        nombre: NOMBRES_PARES[pair] || pair,
        capitalActual,
        posicionAbierta,
      };
    });

    const historicoSesiones = sesionesCerradasParaHistorico.map((sesion) => {
      const trade = mapearTrade(tradesPorSesion.get(sesion.id));
      return {
        sessionId: sesion.id,
        pair: sesion.pair,
        nombre: NOMBRES_PARES[sesion.pair] || sesion.pair,
        capitalUsado: parseFloat(sesion.capital_usado || 0),
        precioEntrada: trade?.precioEntrada ?? null,
        precioSalida: trade?.precioSalida ?? null,
        porcentajeGanancia: trade?.porcentajeGanancia ?? null,
        ganancia: parseFloat(sesion.ganancia || 0),
        razonCierre: sesion.razon_cierre,
        timestampInicio: sesion.timestamp_inicio,
        timestampCierre: sesion.timestamp_cierre,
      };
    });

    return NextResponse.json({
      configurado: true,
      pares,
      historicoSesiones,
      estadoBotMulti,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return NextResponse.json(
      { configurado: true, error: `No se pudo leer el estado de Kraken Multi: ${error.message}` },
      { status: 500 }
    );
  }
}
