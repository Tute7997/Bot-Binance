import { NextResponse } from "next/server";
import { obtenerBalanceRipio } from "../../../lib/ripioSigned";
import { obtenerClienteSupabase } from "../../../lib/supabaseClient";

const HEARTBEAT_LIMITE_SEGUNDOS = 150;

async function obtenerEstadoBot() {
  try {
    const supabase = obtenerClienteSupabase();
    const { data: heartbeat, error } = await supabase
      .from("ripio_heartbeat")
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
  const apiKey = process.env.RIPIO_API_KEY;
  const apiSecret = process.env.RIPIO_SECRET;
  // Ripio no tiene BTC_ARS/ETH_ARS (verificado en vivo): el bot opera USDT_BRL,
  // asi que el capital y el balance se leen en BRL, no en ARS.
  const capitalInicial = parseFloat(process.env.RIPIO_CAPITAL_INICIAL_BRL || "119");

  if (!apiKey || !apiSecret) {
    return NextResponse.json({
      configurado: false,
      mensaje: "Faltan RIPIO_API_KEY / RIPIO_SECRET en dashboard/.env.local.",
    });
  }

  try {
    const [balanceActual, estadoBot] = await Promise.all([
      obtenerBalanceRipio({ apiKey, apiSecret, moneda: "BRL" }),
      obtenerEstadoBot(),
    ]);

    const profitLoss = balanceActual - capitalInicial;
    const profitPercent = capitalInicial > 0 ? (profitLoss / capitalInicial) * 100 : 0;

    return NextResponse.json({
      configurado: true,
      tipo: "ripio",
      capital_inicial: capitalInicial,
      balance_actual: balanceActual,
      profit_loss: profitLoss,
      profit_percent: profitPercent,
      moneda: "BRL",
      estadoBot,
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return NextResponse.json(
      { configurado: true, error: `No se pudo leer el balance de Ripio: ${error.message}` },
      { status: 500 }
    );
  }
}
