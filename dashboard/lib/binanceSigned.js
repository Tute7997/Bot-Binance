import crypto from "crypto";

// Pide el balance de un activo (ej. "USDT") a un endpoint de Binance que
// requiere autenticacion firmada (/api/v3/account). Sirve tanto para
// Binance Testnet como para Binance real, solo cambia baseUrl/apiKey/apiSecret.
export async function obtenerBalanceBinance({ baseUrl, apiKey, apiSecret, asset }) {
  const timestamp = Date.now();
  const query = `timestamp=${timestamp}&recvWindow=10000`;
  const signature = crypto.createHmac("sha256", apiSecret).update(query).digest("hex");

  const respuesta = await fetch(`${baseUrl}/api/v3/account?${query}&signature=${signature}`, {
    headers: { "X-MBX-APIKEY": apiKey },
    cache: "no-store",
  });

  if (!respuesta.ok) {
    const texto = await respuesta.text();
    throw new Error(`Binance respondio ${respuesta.status}: ${texto}`);
  }

  const datos = await respuesta.json();
  const balance = (datos.balances || []).find((b) => b.asset === asset);

  return balance ? parseFloat(balance.free) + parseFloat(balance.locked) : 0;
}
