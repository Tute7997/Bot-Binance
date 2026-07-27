import { NextResponse } from "next/server";
import crypto from "crypto";
import { obtenerBalanceKraken } from "../../../../lib/krakenSigned";
import { obtenerClienteSupabase } from "../../../../lib/supabaseClient";

// Crea una nueva sesion en Supabase con el balance real de Kraken como
// capital inicial. IMPORTANTE: esto NO reinicia kraken-bot.py (no hay
// process manager en este proyecto) - si el bot esta corriendo, hay que
// pararlo (Ctrl+C) y volver a correrlo a mano para que tome esta sesion.
export async function POST() {
  const apiKey = process.env.KRAKEN_API_KEY;
  const apiSecret = process.env.KRAKEN_PRIVATE_KEY;

  if (!apiKey || !apiSecret) {
    return NextResponse.json(
      { error: "Faltan KRAKEN_API_KEY / KRAKEN_PRIVATE_KEY en dashboard/.env.local." },
      { status: 500 }
    );
  }

  try {
    const supabase = obtenerClienteSupabase();

    const { data: estado, error: errorEstado } = await supabase
      .from("account_state")
      .select("*")
      .eq("id", 1)
      .maybeSingle();
    if (errorEstado) throw errorEstado;

    if (estado?.session_activa) {
      return NextResponse.json(
        { error: "Ya hay una sesión activa. Cerrala antes de iniciar una nueva." },
        { status: 400 }
      );
    }

    const capitalInicial = await obtenerBalanceKraken({ apiKey, apiSecret, activo: "ZUSD" });
    const sessionId = crypto.randomUUID();

    const { error: errorInsert } = await supabase.from("sessions").insert({
      session_id: sessionId,
      capital_inicial: capitalInicial,
      status: "active",
    });
    if (errorInsert) throw errorInsert;

    const capitalDepositadoActual = parseFloat(estado?.capital_depositado || 0);
    const nuevoCapitalDepositado = capitalDepositadoActual > 0 ? capitalDepositadoActual : capitalInicial;

    const { error: errorUpsert } = await supabase.from("account_state").upsert({
      id: 1,
      capital_depositado: nuevoCapitalDepositado,
      capital_operando: capitalInicial,
      session_activa: sessionId,
      updated_at: new Date().toISOString(),
    });
    if (errorUpsert) throw errorUpsert;

    return NextResponse.json({
      sessionId,
      capitalInicial,
      mensaje:
        "Sesión creada. Si kraken-bot.py está corriendo, reinicialo (Ctrl+C y volver a correrlo) para que la tome.",
    });
  } catch (error) {
    return NextResponse.json({ error: `No se pudo iniciar la sesión: ${error.message}` }, { status: 500 });
  }
}
