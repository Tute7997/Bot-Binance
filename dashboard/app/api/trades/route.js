import { NextResponse } from "next/server";
import { obtenerClienteSupabase } from "../../../lib/supabaseClient";

// Debe coincidir con PARES en Bot Binance/main.py
const PARES_MONITOREADOS = ["BTCUSDT", "ETHUSDT"];

// Si el heartbeat es mas viejo que esto, se considera que el bot esta inactivo
const HEARTBEAT_LIMITE_SEGUNDOS = 150;

export async function GET() {
  let supabase;
  try {
    supabase = obtenerClienteSupabase();
  } catch (error) {
    return NextResponse.json(
      { error: `Error de configuracion de Supabase: ${error.message}` },
      { status: 500 }
    );
  }

  try {
    // Trades cerrados (para estadisticas e historico)
    const { data: cerrados, error: errorCerrados } = await supabase
      .from("trades")
      .select("*")
      .eq("status", "closed")
      .order("created_at", { ascending: false })
      .limit(200);

    if (errorCerrados) throw errorCerrados;

    // Trades abiertos (para saber que pares tienen posicion activa)
    const { data: abiertos, error: errorAbiertos } = await supabase
      .from("trades")
      .select("*")
      .eq("status", "open");

    if (errorAbiertos) throw errorAbiertos;

    // Heartbeat del bot (puede no existir todavia si el usuario no corrio el SQL)
    let estadoBot = "desconocido";
    let ultimoHeartbeat = null;
    try {
      const { data: heartbeat, error: errorHeartbeat } = await supabase
        .from("bot_heartbeat")
        .select("last_check")
        .eq("id", 1)
        .maybeSingle();

      if (!errorHeartbeat && heartbeat?.last_check) {
        ultimoHeartbeat = heartbeat.last_check;
        const segundosDesdeUltimoCheck =
          (Date.now() - new Date(heartbeat.last_check).getTime()) / 1000;
        estadoBot =
          segundosDesdeUltimoCheck <= HEARTBEAT_LIMITE_SEGUNDOS ? "activo" : "inactivo";
      }
    } catch {
      estadoBot = "desconocido";
    }

    const trades = cerrados || [];

    // --- Ganancia total ---
    const gananciaTotal = trades.reduce((suma, trade) => suma + (trade.profit ?? 0), 0);

    // --- Win rate ---
    const totalCerrados = trades.length;
    const ganadores = trades.filter((trade) => (trade.profit ?? 0) > 0).length;
    const winRate = totalCerrados > 0 ? (ganadores / totalCerrados) * 100 : 0;

    // --- Ultimo trade ---
    const ultimoTrade = trades[0] || null;

    // --- Historico de los ultimos 10 ---
    const historico = trades.slice(0, 10);

    // --- Ganancias por dia (agrupado por fecha UTC de created_at) ---
    const gananciasPorDiaMap = {};
    for (const trade of trades) {
      const fecha = trade.created_at?.slice(0, 10);
      if (!fecha) continue;
      gananciasPorDiaMap[fecha] = (gananciasPorDiaMap[fecha] || 0) + (trade.profit ?? 0);
    }
    const gananciasPorDia = Object.entries(gananciasPorDiaMap)
      .map(([fecha, ganancia]) => ({ fecha, ganancia }))
      .sort((a, b) => a.fecha.localeCompare(b.fecha));

    // --- Pares activos (monitoreados + si tienen posicion abierta) ---
    const paresConPosicionAbierta = new Set((abiertos || []).map((trade) => trade.pair));
    const paresActivos = PARES_MONITOREADOS.map((par) => ({
      par,
      posicionAbierta: paresConPosicionAbierta.has(par),
    }));

    return NextResponse.json({
      estadoBot,
      ultimoHeartbeat,
      gananciaTotal,
      winRate,
      totalCerrados,
      ultimoTrade,
      historico,
      gananciasPorDia,
      paresActivos,
    });
  } catch (error) {
    return NextResponse.json(
      { error: `No se pudo obtener datos de Supabase: ${error.message}` },
      { status: 500 }
    );
  }
}
