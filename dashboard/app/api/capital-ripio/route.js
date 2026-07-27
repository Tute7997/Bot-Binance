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
  const clientId = process.env.RIPIO_CLIENT_ID;
  const clientSecret = process.env.RIPIO_CLIENT_SECRET;
  const endUserId = process.env.RIPIO_END_USER_ID || "ripio-bot-principal";
  // USDT_USD es el unico par confirmado en la documentacion de Ripio B2B,
  // por eso el balance se lee en USD (ver ripio-bot.py para el detalle).
  const capitalInicial = parseFloat(process.env.RIPIO_CAPITAL_INICIAL || "119");

  if (!clientId || !clientSecret) {
    return NextResponse.json({
      configurado: false,
      mensaje: "Faltan RIPIO_CLIENT_ID / RIPIO_CLIENT_SECRET en dashboard/.env.local.",
    });
  }

  try {
    const [balanceActual, estadoBot] = await Promise.all([
      obtenerBalanceRipio({ clientId, clientSecret, endUserId, moneda: "USD" }),
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
      moneda: "USD",
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
