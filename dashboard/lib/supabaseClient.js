import { createClient } from "@supabase/supabase-js";

// Este cliente corre SOLO del lado del servidor (dentro de app/api/trades/route.js).
// Las variables no tienen el prefijo NEXT_PUBLIC_, por eso nunca llegan al
// navegador ni quedan expuestas en el bundle publico.
let supabase = null;

export function obtenerClienteSupabase() {
  if (supabase) return supabase;

  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;

  if (!url || !key) {
    throw new Error(
      "Faltan SUPABASE_URL o SUPABASE_ANON_KEY en las variables de entorno."
    );
  }

  supabase = createClient(url, key);
  return supabase;
}
