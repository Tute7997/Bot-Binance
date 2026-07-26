import { NextResponse } from "next/server";
import { obtenerBalanceBinance } from "../../../lib/binanceSigned";

const BINANCE_REAL_URL = "https://api.binance.com";
const COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=ars";
const TIPO_CAMBIO_RESPALDO_ARS = 900; // se usa solo si CoinGecko falla

async function obtenerTipoCambioArs() {
  try {
    const respuesta = await fetch(COINGECKO_URL, { cache: "no-store" });
    if (!respuesta.ok) throw new Error(`CoinGecko respondio ${respuesta.status}`);
    const datos = await respuesta.json();
    const tasa = datos?.tether?.ars;
    if (!tasa) throw new Error("CoinGecko no devolvio una tasa valida");
    return { tasa, fuente: "CoinGecko" };
  } catch {
    return { tasa: TIPO_CAMBIO_RESPALDO_ARS, fuente: "respaldo fijo" };
  }
}

export async function GET() {
  const apiKey = process.env.BINANCE_REAL_API_KEY;
  const apiSecret = process.env.BINANCE_REAL_SECRET;
  const capitalInicialArs = parseFloat(process.env.BINANCE_REAL_CAPITAL_INICIAL_ARS || "119");

  if (!apiKey || !apiSecret) {
    return NextResponse.json({
      configurado: false,
      mensaje: "Faltan BINANCE_REAL_API_KEY / BINANCE_REAL_SECRET en dashboard/.env.local.",
    });
  }

  try {
    const [balanceActualUsdt, { tasa, fuente }] = await Promise.all([
      obtenerBalanceBinance({ baseUrl: BINANCE_REAL_URL, apiKey, apiSecret, asset: "USDT" }),
      obtenerTipoCambioArs(),
    ]);

    const capitalInicialUsdt = capitalInicialArs / tasa;
    const balanceActualArs = balanceActualUsdt * tasa;

    const profitLossUsdt = balanceActualUsdt - capitalInicialUsdt;
    const profitLossArs = balanceActualArs - capitalInicialArs;
    const profitPercent = capitalInicialArs > 0 ? (profitLossArs / capitalInicialArs) * 100 : 0;

    return NextResponse.json({
      configurado: true,
      tipo: "real",
      moneda_principal: "ARS",
      tipo_cambio_usdt_ars: tasa,

      capital_inicial_ars: capitalInicialArs,
      balance_actual_ars: balanceActualArs,
      profit_loss_ars: profitLossArs,
      profit_percent: profitPercent,

      capital_inicial_usdt: capitalInicialUsdt,
      balance_actual_usdt: balanceActualUsdt,
      profit_loss_usdt: profitLossUsdt,

      timestamp: new Date().toISOString(),
      fuente_tipo_cambio: fuente,
    });
  } catch (error) {
    return NextResponse.json(
      { configurado: true, error: `No se pudo leer el balance de Binance real: ${error.message}` },
      { status: 500 }
    );
  }
}
