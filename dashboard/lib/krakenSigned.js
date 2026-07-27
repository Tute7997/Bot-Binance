import crypto from "crypto";

const KRAKEN_BASE_URL = "https://api.kraken.com";
const DEBUG_AUTH = process.env.KRAKEN_DEBUG_AUTH === "true";

// Firma segun el esquema oficial de Kraken:
// HMAC-SHA512(urlpath + SHA256(nonce + POST data), secret en base64) -> base64.
function firmarKraken(urlpath, params, secret) {
  const cuerpoCodificado = new URLSearchParams(params).toString();
  const hashSha256 = crypto
    .createHash("sha256")
    .update(params.nonce + cuerpoCodificado)
    .digest();

  const mensaje = Buffer.concat([Buffer.from(urlpath), hashSha256]);
  const firma = crypto.createHmac("sha512", Buffer.from(secret, "base64")).update(mensaje).digest("base64");
  return firma;
}

async function peticionPrivadaKraken(path, apiKey, apiSecret, datosExtra = {}) {
  const params = { nonce: Date.now().toString(), ...datosExtra };
  const firma = firmarKraken(path, params, apiSecret);

  if (DEBUG_AUTH) {
    console.log(`[KRAKEN_DEBUG_AUTH] POST ${path} | API-Key=${apiKey.slice(0, 8)}... | nonce=${params.nonce}`);
  }

  const respuesta = await fetch(`${KRAKEN_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "API-Key": apiKey,
      "API-Sign": firma,
      "Content-Type": "application/x-www-form-urlencoded",
    },
    body: new URLSearchParams(params).toString(),
    cache: "no-store",
  });

  const texto = await respuesta.text();

  if (DEBUG_AUTH) {
    console.log(`[KRAKEN_DEBUG_AUTH] Respuesta ${respuesta.status}: ${texto.slice(0, 300)}`);
  }

  if (!respuesta.ok) {
    throw new Error(`Kraken respondio ${respuesta.status}: ${texto}`);
  }

  const datos = JSON.parse(texto);
  if (datos.error && datos.error.length > 0) {
    throw new Error(`Kraken devolvio un error: ${datos.error.join(", ")}`);
  }

  return datos.result;
}

// Pide el balance de un activo (ej. "ZUSD") a la cuenta real de Kraken.
export async function obtenerBalanceKraken({ apiKey, apiSecret, activo }) {
  const resultado = await peticionPrivadaKraken("/0/private/Balance", apiKey, apiSecret);
  return parseFloat(resultado?.[activo] || 0);
}
