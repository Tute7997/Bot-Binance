"use client";

import { useEffect, useState } from "react";

const INTERVALO_ACTUALIZACION_MS = 5000;

function formatearUSD(valor) {
  if (valor === null || valor === undefined) return "-";
  return `$${valor.toLocaleString("es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatearFecha(fechaIso) {
  if (!fechaIso) return "-";
  return new Date(fechaIso).toLocaleString("es-AR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function claseColorGanancia(valor) {
  if (valor === null || valor === undefined) return "text-zinc-400";
  return valor >= 0 ? "text-emerald-400" : "text-red-400";
}

function formatearDuracionSegundos(segundosTotales) {
  if (segundosTotales === null || segundosTotales === undefined || segundosTotales < 0) return "-";
  const horas = Math.floor(segundosTotales / 3600);
  const minutos = Math.floor((segundosTotales % 3600) / 60);
  const segundos = Math.floor(segundosTotales % 60);
  return `${horas}h ${minutos}m ${segundos}s`;
}

export default function PaginaDashboard() {
  const [datos, setDatos] = useState(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(null);
  const [accionCargando, setAccionCargando] = useState(false);
  const [accionError, setAccionError] = useState(null);
  const [, forzarTick] = useState(0);

  async function traerDatos() {
    try {
      const respuesta = await fetch("/api/capital-kraken", { cache: "no-store" });
      const json = await respuesta.json();

      if (!respuesta.ok || json.error) {
        throw new Error(json.error || "Error desconocido consultando el servidor");
      }

      setDatos(json);
      setError(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setCargando(false);
    }
  }

  useEffect(() => {
    traerDatos();
    const intervalo = setInterval(traerDatos, INTERVALO_ACTUALIZACION_MS);
    const tick = setInterval(() => forzarTick((n) => n + 1), 1000);

    return () => {
      clearInterval(intervalo);
      clearInterval(tick);
    };
  }, []);

  async function iniciarSesion() {
    setAccionCargando(true);
    setAccionError(null);
    try {
      const respuesta = await fetch("/api/capital-kraken/start", { method: "POST" });
      const json = await respuesta.json();
      if (!respuesta.ok) throw new Error(json.error || "No se pudo iniciar la sesión.");
      await traerDatos();
    } catch (err) {
      setAccionError(err.message);
    } finally {
      setAccionCargando(false);
    }
  }

  async function cerrarSesion() {
    if (!window.confirm("¿Seguro que querés cerrar la sesión actual? Esta acción no se puede deshacer.")) {
      return;
    }
    setAccionCargando(true);
    setAccionError(null);
    try {
      const respuesta = await fetch("/api/capital-kraken/close", { method: "POST" });
      const json = await respuesta.json();
      if (!respuesta.ok) throw new Error(json.error || "No se pudo cerrar la sesión.");
      await traerDatos();
    } catch (err) {
      setAccionError(err.message);
    } finally {
      setAccionCargando(false);
    }
  }

  if (cargando && !datos) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-lg text-zinc-400">Cargando...</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
      <Encabezado estadoBot={datos?.estadoBot} />

      {error && (
        <div className="mb-6 rounded-lg border border-red-800 bg-red-950/60 px-4 py-3 text-sm text-red-300">
          No se pudo conectar con Kraken/Supabase: {error}
        </div>
      )}

      {datos?.configurado === false && (
        <div className="mb-6 rounded-lg border border-amber-700/50 bg-amber-950/40 px-4 py-3 text-sm text-amber-300">
          Credenciales de Kraken no configuradas todavía. {datos.mensaje}
        </div>
      )}

      {datos && datos.configurado !== false && (
        <div className="space-y-6">
          <SeccionSesionActual
            datos={datos}
            accionCargando={accionCargando}
            accionError={accionError}
            onIniciar={iniciarSesion}
            onCerrar={cerrarSesion}
          />
          <SeccionCapitalDepositado datos={datos} />
          <SeccionHistorico historico={datos.historicoSesiones} />

          <p className="text-center text-xs text-zinc-600">
            El reinicio de <code>kraken-bot.py</code> es manual: después de iniciar o cerrar una sesión acá,
            tenés que parar el script (Ctrl+C) y volver a correrlo para que la tome.
          </p>
        </div>
      )}
    </main>
  );
}

function Encabezado({ estadoBot }) {
  const operando = estadoBot === "operando";
  const inactivo = estadoBot === "inactivo";

  const colorPill = operando
    ? "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30"
    : inactivo
    ? "bg-red-500/15 text-red-400 ring-red-500/30"
    : "bg-zinc-500/15 text-zinc-400 ring-zinc-500/30";

  const texto = operando ? "Operando" : inactivo ? "Inactivo" : "Desconocido";

  return (
    <div className="mb-8 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100 sm:text-3xl">💰 Dashboard Bot Kraken</h1>
        <p className="text-sm text-zinc-500">Kraken · BTC/USD · ETH/USD</p>
      </div>
      <span
        className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ring-1 ${colorPill}`}
      >
        <span
          className={`h-2 w-2 rounded-full ${
            operando ? "bg-emerald-400" : inactivo ? "bg-red-400" : "bg-zinc-400"
          }`}
        />
        Estado del bot: {texto}
      </span>
    </div>
  );
}

function Tarjeta({ titulo, children }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      <h2 className="mb-3 text-sm font-medium text-zinc-400">{titulo}</h2>
      {children}
    </div>
  );
}

function Estadistica({ etiqueta, valor }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{etiqueta}</p>
      <p className="mt-0.5 text-base font-semibold text-zinc-100">{valor}</p>
    </div>
  );
}

function SeccionSesionActual({ datos, accionCargando, accionError, onIniciar, onCerrar }) {
  const sesion = datos.sesionActiva;

  if (!sesion) {
    return (
      <Tarjeta titulo="Sesión actual">
        <p className="text-sm text-zinc-500">
          No hay ninguna sesión activa. Balance actual en Kraken: {formatearUSD(datos.balanceActual)}.
        </p>
        <button
          type="button"
          onClick={onIniciar}
          disabled={accionCargando}
          className="mt-4 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {accionCargando ? "Iniciando..." : "▶️ Iniciar Sesión"}
        </button>
        {accionError && <p className="mt-2 text-sm text-red-400">{accionError}</p>}
      </Tarjeta>
    );
  }

  const segundosEnSesion = Math.max(0, Math.floor((Date.now() - new Date(sesion.startTime).getTime()) / 1000));

  return (
    <Tarjeta titulo="Sesión actual">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Estadistica etiqueta="Capital operando" valor={formatearUSD(sesion.capitalInicial)} />
        <Estadistica etiqueta="Balance en vivo" valor={formatearUSD(datos.balanceActual)} />
        <Estadistica
          etiqueta="Profit"
          valor={
            <span className={claseColorGanancia(sesion.profit)}>
              {sesion.profit >= 0 ? "+" : ""}
              {formatearUSD(sesion.profit)}
            </span>
          }
        />
        <Estadistica
          etiqueta="% Rendimiento"
          valor={
            <span className={claseColorGanancia(sesion.profitPercent)}>
              {sesion.profitPercent >= 0 ? "+" : ""}
              {sesion.profitPercent.toFixed(2)}%
            </span>
          }
        />
        <Estadistica etiqueta="Tiempo en sesión" valor={formatearDuracionSegundos(segundosEnSesion)} />
      </div>

      <div className="mt-5">
        <button
          type="button"
          onClick={onCerrar}
          disabled={accionCargando || datos.hayPosicionAbierta}
          title={datos.hayPosicionAbierta ? "Hay una posición abierta: esperá a que se cierre por TP/SL." : ""}
          className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {accionCargando ? "Cerrando..." : "⏹️ Cerrar Sesión"}
        </button>
        {datos.hayPosicionAbierta && (
          <p className="mt-2 text-xs text-amber-400">
            Hay una posición abierta ahora mismo — no se puede cerrar la sesión hasta que se resuelva por TP/SL.
          </p>
        )}
        {accionError && <p className="mt-2 text-sm text-red-400">{accionError}</p>}
      </div>
    </Tarjeta>
  );
}

function SeccionCapitalDepositado({ datos }) {
  return (
    <Tarjeta titulo="Capital depositado (acumulado)">
      <p className="text-3xl font-bold text-emerald-400">{formatearUSD(datos.capitalDepositado)}</p>
      <p className="mt-2 text-xs text-zinc-500">Se suma el profit cada vez que cerrás una sesión.</p>
    </Tarjeta>
  );
}

function SeccionHistorico({ historico }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-medium text-zinc-400">Histórico de sesiones</h2>

      {!historico || historico.length === 0 ? (
        <p className="text-sm text-zinc-500">Todavía no se cerró ninguna sesión.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-2 pr-4 font-medium">Fecha de cierre</th>
                <th className="py-2 pr-4 font-medium">Duración</th>
                <th className="py-2 pr-4 font-medium">Capital</th>
                <th className="py-2 pr-4 font-medium">Profit</th>
                <th className="py-2 pr-4 font-medium">%</th>
              </tr>
            </thead>
            <tbody>
              {historico.map((sesion) => {
                const duracionSegundos =
                  sesion.endTime && sesion.startTime
                    ? (new Date(sesion.endTime).getTime() - new Date(sesion.startTime).getTime()) / 1000
                    : null;

                return (
                  <tr key={sesion.sessionId} className="border-b border-zinc-800/60 last:border-0">
                    <td className="py-2 pr-4 text-zinc-400">{formatearFecha(sesion.endTime)}</td>
                    <td className="py-2 pr-4 text-zinc-300">{formatearDuracionSegundos(duracionSegundos)}</td>
                    <td className="py-2 pr-4 text-zinc-300">{formatearUSD(sesion.capitalInicial)}</td>
                    <td className={`py-2 pr-4 font-medium ${claseColorGanancia(sesion.profit)}`}>
                      {sesion.profit >= 0 ? "+" : ""}
                      {formatearUSD(sesion.profit)}
                    </td>
                    <td className={`py-2 pr-4 font-medium ${claseColorGanancia(sesion.profitPercent)}`}>
                      {sesion.profitPercent >= 0 ? "+" : ""}
                      {sesion.profitPercent.toFixed(2)}%
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
