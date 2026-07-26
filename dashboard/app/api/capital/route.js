import { NextResponse } from "next/server";
import { obtenerBalanceBinance } from "../../../lib/binanceSigned";

const BINANCE_TESTNET_URL = "https://testnet.binance.vision";
const CAPITAL_INICIAL_TESTNET = 10000; // USDT, fijo (mismo capital que asume main.py)

export async function GET() {
  const apiKey = process.env.BINANCE_API_KEY;
  const apiSecret = process.env.BINANCE_API_SECRET;

  if (!apiKey || !apiSecret) {
    return NextResponse.json(
      { error: "Faltan BINANCE_API_KEY o BINANCE_API_SECRET en las variables de entorno." },
      { status: 500 }
    );
  }

  try {
    const balanceActual = await obtenerBalanceBinance({
      baseUrl: BINANCE_TESTNET_URL,
      apiKey,
      apiSecret,
      asset: "USDT",
    });

    const profitLoss = balanceActual - CAPITAL_INICIAL_TESTNET;
    const profitPercent = (profitLoss / CAPITAL_INICIAL_TESTNET) * 100;

    return NextResponse.json({
      tipo: "testnet",
      capital_inicial: CAPITAL_INICIAL_TESTNET,
      balance_actual: balanceActual,
      profit_loss: profitLoss,
      profit_percent: profitPercent,
      moneda: "USDT",
      timestamp: new Date().toISOString(),
    });
  } catch (error) {
    return NextResponse.json(
      { error: `No se pudo leer el balance de Binance Testnet: ${error.message}` },
      { status: 500 }
    );
  }
}
