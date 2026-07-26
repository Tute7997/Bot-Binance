import crypto from "crypto";

const RIPIO_BASE_URL = "https://api.ripiotrade.co/v4";

// Firma segun el esquema de Ripio Trade: Signature = HMAC-SHA256 en Base64
// de (Timestamp + Metodo + Path + JSON del body), usando el Secret Key.
async function peticionRipio(metodo, path, apiKey, apiSecret, cuerpo = null) {
  const cuerpoJson = cuerpo !== null ? JSON.stringify(cuerpo) : "";
  const timestamp = Date.now().toString();
  const mensaje = `${timestamp}${metodo}${path}${cuerpoJson}`;
  const firma = crypto.createHmac("sha256", apiSecret).update(mensaje).digest("base64");

  const respuesta = await fetch(`${RIPIO_BASE_URL}${path}`, {
    method: metodo,
    headers: {
      "Content-Type": "application/json",
      Authorization: apiKey,
      Timestamp: timestamp,
      Signature: firma,
    },
    body: cuerpo !== null ? cuerpoJson : undefined,
    cache: "no-store",
  });

  if (!respuesta.ok) {
    const texto = await respuesta.text();
    throw new Error(`Ripio respondio ${respuesta.status}: ${texto}`);
  }

  const datos = await respuesta.json();
  if (datos.error_code) {
    throw new Error(`Ripio devolvio un error: ${datos.message}`);
  }

  return datos.data;
}

// Pide el balance de una moneda (ej. "ARS") a la cuenta real de Ripio.
export async function obtenerBalanceRipio({ apiKey, apiSecret, moneda }) {
  let balances = (await peticionRipio("GET", "/user/balances", apiKey, apiSecret)) || [];

  // La respuesta de Ripio a veces anida el array de balances; se aplana por las dudas.
  while (Array.isArray(balances[0])) {
    balances = balances.flat();
  }

  const balance = balances.find((b) => b.currency_code === moneda);
  return balance ? parseFloat(balance.available_amount) + parseFloat(balance.locked_amount) : 0;
}
