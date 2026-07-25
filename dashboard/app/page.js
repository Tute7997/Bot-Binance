"use client";

import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Cell,
} from "recharts";

const INTERVALO_ACTUALIZACION_MS = 5000;

function formatearUSD(valor) {
  if (valor === null || valor === undefined) return "-";
  return valor.toLocaleString("es-AR", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
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

function etiquetaMotivo(motivo) {
  if (motivo === "TP") return "Take Profit";
  if (motivo === "SL") return "Stop Loss";
  return motivo || "-";
}

export default function PaginaDashboard() {
  const [datos, setDatos] = useState(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    let activo = true;

    async function traerDatos() {
      try {
        const respuesta = await fetch("/api/trades", { cache: "no-store" });
        const json = await respuesta.json();

        if (!respuesta.ok || json.error) {
          throw new Error(json.error || "Error desconocido consultando el servidor");
        }

        if (activo) {
          setDatos(json);
          setError(null);
        }
      } catch (err) {
        if (activo) setError(err.message);
      } finally {
        if (activo) setCargando(false);
      }
    }

    traerDatos();
    const intervalo = setInterval(traerDatos, INTERVALO_ACTUALIZACION_MS);

    return () => {
      activo = false;
      clearInterval(intervalo);
    };
  }, []);

  if (cargando && !datos) {
    return (
      <main className="flex min-h-screen items-center justify-center">
        <p className="text-lg text-zinc-400">Cargando...</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <Encabezado estadoBot={datos?.estadoBot} />

      {error && (
        <div className="mb-6 rounded-lg border border-red-800 bg-red-950/60 px-4 py-3 text-sm text-red-300">
          No se pudo conectar con la base de datos: {error}
        </div>
      )}

      {datos && (
        <>
          <TarjetasResumen datos={datos} />

          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <UltimoTrade trade={datos.ultimoTrade} />
            <GraficoGananciasPorDia datos={datos.gananciasPorDia} />
          </div>

          <div className="mt-6">
            <TablaHistorico historico={datos.historico} />
          </div>
        </>
      )}
    </main>
  );
}

function Encabezado({ estadoBot }) {
  const activo = estadoBot === "activo";
  const inactivo = estadoBot === "inactivo";

  const colorPill = activo
    ? "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30"
    : inactivo
    ? "bg-red-500/15 text-red-400 ring-red-500/30"
    : "bg-zinc-500/15 text-zinc-400 ring-zinc-500/30";

  const texto = activo ? "Activo" : inactivo ? "Inactivo" : "Desconocido";

  return (
    <div className="mb-8 flex flex-col items-start justify-between gap-3 sm:flex-row sm:items-center">
      <div>
        <h1 className="text-2xl font-bold text-zinc-100 sm:text-3xl">
          Dashboard Bot Trading
        </h1>
        <p className="text-sm text-zinc-500">Binance Testnet · BTC/USDT · ETH/USDT</p>
      </div>
      <span
        className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-sm font-medium ring-1 ${colorPill}`}
      >
        <span
          className={`h-2 w-2 rounded-full ${
            activo ? "bg-emerald-400" : inactivo ? "bg-red-400" : "bg-zinc-400"
          }`}
        />
        Estado del bot: {texto}
      </span>
    </div>
  );
}

function TarjetasResumen({ datos }) {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <Tarjeta titulo="Ganancia / Pérdida total">
        <p className={`text-3xl font-bold ${claseColorGanancia(datos.gananciaTotal)}`}>
          {formatearUSD(datos.gananciaTotal)}
        </p>
        <p className="mt-1 text-xs text-zinc-500">{datos.totalCerrados} operaciones cerradas</p>
      </Tarjeta>

      <Tarjeta titulo="Win rate">
        <p className="text-3xl font-bold text-zinc-100">{datos.winRate.toFixed(1)}%</p>
        <p className="mt-1 text-xs text-zinc-500">Operaciones ganadoras</p>
      </Tarjeta>

      <Tarjeta titulo="Pares activos">
        <div className="flex flex-wrap gap-2">
          {datos.paresActivos.map(({ par, posicionAbierta }) => (
            <span
              key={par}
              className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ring-1 ${
                posicionAbierta
                  ? "bg-emerald-500/15 text-emerald-400 ring-emerald-500/30"
                  : "bg-zinc-500/15 text-zinc-400 ring-zinc-500/30"
              }`}
            >
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  posicionAbierta ? "bg-emerald-400" : "bg-zinc-500"
                }`}
              />
              {par}
            </span>
          ))}
        </div>
        <p className="mt-2 text-xs text-zinc-500">Verde = posición abierta ahora mismo</p>
      </Tarjeta>
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

function UltimoTrade({ trade }) {
  if (!trade) {
    return (
      <Tarjeta titulo="Último trade">
        <p className="text-sm text-zinc-500">Todavía no hay trades cerrados.</p>
      </Tarjeta>
    );
  }

  return (
    <Tarjeta titulo="Último trade">
      <div className="space-y-2 text-sm">
        <FilaDato etiqueta="Fecha" valor={formatearFecha(trade.created_at)} />
        <FilaDato etiqueta="Par" valor={trade.pair} />
        <FilaDato etiqueta="Entrada" valor={formatearUSD(trade.entry_price)} />
        <FilaDato etiqueta="Salida" valor={formatearUSD(trade.exit_price)} />
        <FilaDato
          etiqueta="Ganancia"
          valor={
            <span className={claseColorGanancia(trade.profit)}>
              {formatearUSD(trade.profit)} ({(trade.profit_percent ?? 0).toFixed(2)}%)
            </span>
          }
        />
        <FilaDato etiqueta="Motivo de cierre" valor={etiquetaMotivo(trade.reason)} />
      </div>
    </Tarjeta>
  );
}

function FilaDato({ etiqueta, valor }) {
  return (
    <div className="flex items-center justify-between border-b border-zinc-800/60 pb-2 last:border-0 last:pb-0">
      <span className="text-zinc-500">{etiqueta}</span>
      <span className="font-medium text-zinc-100">{valor}</span>
    </div>
  );
}

function GraficoGananciasPorDia({ datos }) {
  const hayDatos = datos && datos.length > 0;

  return (
    <Tarjeta titulo="Ganancias por día">
      {hayDatos ? (
        <div className="h-56 w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={datos}>
              <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
              <XAxis dataKey="fecha" stroke="#71717a" fontSize={12} />
              <YAxis stroke="#71717a" fontSize={12} />
              <Tooltip
                contentStyle={{ background: "#18181b", border: "1px solid #3f3f46" }}
                labelStyle={{ color: "#e4e4e7" }}
                formatter={(valor) => formatearUSD(valor)}
              />
              <Bar dataKey="ganancia" radius={[4, 4, 0, 0]}>
                {datos.map((entrada, indice) => (
                  <Cell
                    key={indice}
                    fill={entrada.ganancia >= 0 ? "#34d399" : "#f87171"}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="text-sm text-zinc-500">Todavía no hay suficientes trades para graficar.</p>
      )}
    </Tarjeta>
  );
}

function TablaHistorico({ historico }) {
  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-medium text-zinc-400">Histórico (últimos 10 trades)</h2>

      {!historico || historico.length === 0 ? (
        <p className="text-sm text-zinc-500">Todavía no hay trades cerrados.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead>
              <tr className="border-b border-zinc-800 text-zinc-500">
                <th className="py-2 pr-4 font-medium">Par</th>
                <th className="py-2 pr-4 font-medium">Fecha</th>
                <th className="py-2 pr-4 font-medium">Entrada</th>
                <th className="py-2 pr-4 font-medium">Salida</th>
                <th className="py-2 pr-4 font-medium">Ganancia</th>
                <th className="py-2 pr-4 font-medium">Motivo</th>
              </tr>
            </thead>
            <tbody>
              {historico.map((trade) => (
                <tr key={trade.id} className="border-b border-zinc-800/60 last:border-0">
                  <td className="py-2 pr-4 font-medium text-zinc-100">{trade.pair}</td>
                  <td className="py-2 pr-4 text-zinc-400">{formatearFecha(trade.created_at)}</td>
                  <td className="py-2 pr-4 text-zinc-300">{formatearUSD(trade.entry_price)}</td>
                  <td className="py-2 pr-4 text-zinc-300">{formatearUSD(trade.exit_price)}</td>
                  <td className={`py-2 pr-4 font-medium ${claseColorGanancia(trade.profit)}`}>
                    {formatearUSD(trade.profit)}
                  </td>
                  <td className="py-2 pr-4 text-zinc-400">{etiquetaMotivo(trade.reason)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
