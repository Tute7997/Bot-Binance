import { NextResponse } from "next/server";
import { obtenerBalanceKraken } from "../../../lib/krakenSigned";
import { obtenerClienteSupabase } from "../../../lib/supabaseClient";

const HEARTBEAT_LIMITE_SEGUNDOS = 150;

async function obtenerEstadoBot() {
  try {
    const supabase = obtenerClienteSupabase();
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
  const capitalInicial = parseFloat(process.env.KRAKEN_CAPITAL_INICIAL || "0");

  if (!apiKey || !apiSecret) {
    return NextResponse.json({
      configurado: false,
      mensaje: "Faltan KRAKEN_API_KEY / KRAKEN_PRIVATE_KEY en dashboard/.env.local.",
    });
  }

  try {
    const [balanceActual, estadoBot] = await Promise.all([
      obtenerBalanceKraken({ apiKey, apiSecret, activo: "ZUSD" }),
      obtenerEstadoBot(),
    ]);

    const profitLoss = balanceActual - capitalInicial;
    const profitPercent = capitalInicial > 0 ? (profitLoss / capitalInicial) * 100 : 0;

    return NextResponse.json({
      configurado: true,
      tipo: "kraken",
      capital_inicial: capitalInicial,
      balance_actual: balanceActual,
      profit_loss: profitLoss,
      profit_percent: profitPercent,
      moneda: "USD",
      estadoBot,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return NextResponse.json(
      { configurado: true, error: `No se pudo leer el balance de Kraken: ${error.message}` },
      { status: 500 }
    );
  }
}
