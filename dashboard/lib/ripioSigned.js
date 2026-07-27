const RIPIO_BASE_URL = "https://b2b-api.ripio.com";
const DEBUG_AUTH = process.env.RIPIO_DEBUG_AUTH === "true";

// Cache del access token OAuth2 a nivel de modulo (se renueva solo cuando vence).
let cacheToken = { accessToken: null, venceEn: 0 };

// Pide (o reutiliza si no vencio) un access token OAuth2 client_credentials,
// segun el esquema real de la API B2B / Crypto as a Service de Ripio.
async function obtenerTokenRipio(clientId, clientSecret) {
  const ahora = Date.now() / 1000;
  if (cacheToken.accessToken && ahora < cacheToken.venceEn) {
    return cacheToken.accessToken;
  }

  const credenciales = Buffer.from(`${clientId}:${clientSecret}`).toString("base64");

  if (DEBUG_AUTH) {
    console.log(`[RIPIO_DEBUG_AUTH] Pidiendo token OAuth2 (client_id=${clientId.slice(0, 8)}...)`);
  }

  const respuesta = await fetch(`${RIPIO_BASE_URL}/oauth2/token/`, {
    method: "POST",
    headers: {
      Authorization: `Basic ${credenciales}`,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: "grant_type=client_credentials",
    cache: "no-store",
  });

  const texto = await respuesta.text();

  if (DEBUG_AUTH) {
    console.log(`[RIPIO_DEBUG_AUTH] Respuesta token ${respuesta.status}: ${texto.slice(0, 300)}`);
  }

  if (!respuesta.ok) {
    throw new Error(`Ripio (token) respondio ${respuesta.status}: ${texto}`);
  }

  const datos = JSON.parse(texto);
  cacheToken = {
    accessToken: datos.access_token,
    venceEn: ahora + (datos.expires_in || 300) - 30,
  };

  return cacheToken.accessToken;
}

async function peticionRipioB2B(metodo, path, token, cuerpo = null) {
  const respuesta = await fetch(`${RIPIO_BASE_URL}${path}`, {
    method: metodo,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: cuerpo !== null ? JSON.stringify(cuerpo) : undefined,
    cache: "no-store",
  });

  const texto = await respuesta.text();

  if (DEBUG_AUTH) {
    console.log(`[RIPIO_DEBUG_AUTH] ${metodo} ${path} -> ${respuesta.status}: ${texto.slice(0, 300)}`);
  }

  if (!respuesta.ok) {
    throw new Error(`Ripio respondio ${respuesta.status}: ${texto}`);
  }

  return texto ? JSON.parse(texto) : null;
}

// Pide el balance de una moneda (ej. "USD") para el End User configurado.
export async function obtenerBalanceRipio({ clientId, clientSecret, endUserId, moneda }) {
  const token = await obtenerTokenRipio(clientId, clientSecret);
  const datos = await peticionRipioB2B("GET", `/api/v1/end-users/${endUserId}/balances/`, token);
  const balances = datos?.data || [];

  const balance = balances.find((b) => b.currency === moneda);
  return balance ? parseFloat(balance.balance) : 0;
}
