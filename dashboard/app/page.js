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

function formatearMoneda(valor, moneda, decimales = 2) {
  if (valor === null || valor === undefined) return "-";
  return `${valor.toLocaleString("es-AR", {
    minimumFractionDigits: decimales,
    maximumFractionDigits: decimales,
  })} ${moneda}`;
}

function formatearHaceCuanto(timestampIso) {
  if (!timestampIso) return "-";
  const segundos = Math.max(0, Math.floor((Date.now() - new Date(timestampIso).getTime()) / 1000));
  if (segundos < 60) return `hace ${segundos} segundos`;
  return `hace ${Math.floor(segundos / 60)} min`;
}

export default function PaginaDashboard() {
  const [tabActiva, setTabActiva] = useState("testnet");
  const [datos, setDatos] = useState(null);
  const [capitalTestnet, setCapitalTestnet] = useState(null);
  const [capitalReal, setCapitalReal] = useState(null);
  const [cargando, setCargando] = useState(true);
  const [error, setError] = useState(null);
  const [, forzarTick] = useState(0);

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

    async function traerCapitalTestnet() {
      try {
        const respuesta = await fetch("/api/capital", { cache: "no-store" });
        const json = await respuesta.json();
        if (activo && respuesta.ok && !json.error) setCapitalTestnet(json);
      } catch {
        // si falla, se mantiene el ultimo valor bueno conocido
      }
    }

    async function traerCapitalReal() {
      try {
        const respuesta = await fetch("/api/capital-real", { cache: "no-store" });
        const json = await respuesta.json();
        if (activo) setCapitalReal(json);
      } catch {
        // si falla, se mantiene el ultimo valor bueno conocido
      }
    }

    function actualizarTodo() {
      return Promise.all([traerDatos(), traerCapitalTestnet(), traerCapitalReal()]);
    }

    actualizarTodo();
    const intervalo = setInterval(actualizarTodo, INTERVALO_ACTUALIZACION_MS);
    const tick = setInterval(() => forzarTick((n) => n + 1), 1000);

    return () => {
      activo = false;
      clearInterval(intervalo);
      clearInterval(tick);
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

      <TabsSelector tabActiva={tabActiva} onCambiar={setTabActiva} />

      {error && (
        <div className="mb-6 rounded-lg border border-red-800 bg-red-950/60 px-4 py-3 text-sm text-red-300">
          No se pudo conectar con la base de datos: {error}
        </div>
      )}

      {tabActiva === "testnet" && datos && (
        <>
          <div className="mb-6">
            <TarjetaCapital datos={capitalTestnet} />
          </div>

          <TarjetasResumen datos={datos} />

          <div className="mt-6">
            <OperacionesAbiertas operaciones={datos.operacionesAbiertas} />
          </div>

          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <UltimoTrade trade={datos.ultimoTrade} />
            <GraficoGananciasPorDia datos={datos.gananciasPorDia} />
          </div>

          <div className="mt-6">
            <TablaHistorico historico={datos.historico} />
          </div>

          <div className="mt-6 inline-flex items-center gap-2 rounded-full bg-emerald-500/15 px-3 py-1 text-sm font-medium text-emerald-400 ring-1 ring-emerald-500/30">
            ✅ BOT OPERANDO
          </div>
        </>
      )}

      {tabActiva === "real" && (
        <>
          <div className="mb-6">
            <TarjetaCapitalReal datos={capitalReal} />
          </div>

          <div className="rounded-lg border border-amber-700/50 bg-amber-950/40 px-4 py-3 text-sm text-amber-300">
            ⚠️ Bot NO opera aquí todavía. Solo lectura. Cuando testnet sea rentable, cambiaremos a esta cuenta.
          </div>
        </>
      )}
    </main>
  );
}

function TabsSelector({ tabActiva, onCambiar }) {
  const base = "rounded-t-lg px-4 py-2 text-sm font-medium border-b-2 transition-colors";
  return (
    <div className="mb-6 flex gap-2 border-b border-zinc-800">
      <button
        type="button"
        onClick={() => onCambiar("testnet")}
        className={`${base} ${
          tabActiva === "testnet"
            ? "border-emerald-400 text-emerald-400"
            : "border-transparent text-zinc-500 hover:text-zinc-300"
        }`}
      >
        🟢 TESTNET (Ficticio)
      </button>
      <button
        type="button"
        onClick={() => onCambiar("real")}
        className={`${base} ${
          tabActiva === "real"
            ? "border-sky-400 text-sky-400"
            : "border-transparent text-zinc-500 hover:text-zinc-300"
        }`}
      >
        🏦 REAL (Lectura)
      </button>
    </div>
  );
}

function EstadisticaCapital({ etiqueta, valor }) {
  return (
    <div>
      <p className="text-xs text-zinc-500">{etiqueta}</p>
      <p className="mt-0.5 text-base font-semibold text-zinc-100">{valor}</p>
    </div>
  );
}

function TarjetaCapital({ datos }) {
  if (!datos) {
    return (
      <Tarjeta titulo="🟢 Capital Testnet">
        <p className="text-sm text-zinc-500">Cargando balance de Binance Testnet...</p>
      </Tarjeta>
    );
  }

  return (
    <Tarjeta titulo="🟢 Capital Testnet">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <EstadisticaCapital etiqueta="Depositado" valor={formatearMoneda(datos.capital_inicial, datos.moneda)} />
        <EstadisticaCapital
          etiqueta="Balance actual"
          valor={formatearMoneda(datos.balance_actual, datos.moneda, 2)}
        />
        <EstadisticaCapital
          etiqueta="Profit total"
          valor={
            <span className={claseColorGanancia(datos.profit_loss)}>
              {datos.profit_loss >= 0 ? "+" : ""}
              {formatearMoneda(datos.profit_loss, datos.moneda)}
            </span>
          }
        />
        <EstadisticaCapital
          etiqueta="% Rendimiento"
          valor={
            <span className={claseColorGanancia(datos.profit_percent)}>
              {datos.profit_percent >= 0 ? "+" : ""}
              {datos.profit_percent.toFixed(3)}%
            </span>
          }
        />
      </div>
      <p className="mt-3 text-xs text-zinc-500">Actualizado {formatearHaceCuanto(datos.timestamp)}</p>
    </Tarjeta>
  );
}

function TarjetaCapitalReal({ datos }) {
  if (!datos) {
    return (
      <Tarjeta titulo="🏦 Capital Real (en vivo)">
        <p className="text-sm text-zinc-500">Cargando...</p>
      </Tarjeta>
    );
  }

  if (datos.configurado === false) {
    return (
      <Tarjeta titulo="🏦 Capital Real (en vivo)">
        <p className="text-sm text-zinc-500">
          Credenciales de Binance real no configuradas todavía. {datos.mensaje}
        </p>
      </Tarjeta>
    );
  }

  if (datos.error) {
    return (
      <Tarjeta titulo="🏦 Capital Real (en vivo)">
        <p className="text-sm text-red-400">{datos.error}</p>
      </Tarjeta>
    );
  }

  return (
    <Tarjeta titulo="🏦 Capital Real (en vivo)">
      <div className="space-y-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-sky-400">
            💵 En pesos argentinos (ARS)
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <EstadisticaCapital etiqueta="Depositado" valor={formatearMoneda(datos.capital_inicial_ars, "ARS")} />
            <EstadisticaCapital
              etiqueta="Balance actual"
              valor={formatearMoneda(datos.balance_actual_ars, "ARS")}
            />
            <EstadisticaCapital
              etiqueta="Profit total"
              valor={
                <span className={claseColorGanancia(datos.profit_loss_ars)}>
                  {datos.profit_loss_ars >= 0 ? "+" : ""}
                  {formatearMoneda(datos.profit_loss_ars, "ARS")}
                </span>
              }
            />
            <EstadisticaCapital
              etiqueta="% Rendimiento"
              valor={
                <span className={claseColorGanancia(datos.profit_percent)}>
                  {datos.profit_percent >= 0 ? "+" : ""}
                  {datos.profit_percent.toFixed(2)}%
                </span>
              }
            />
          </div>
        </div>

        <p className="text-xs text-zinc-500">
          🔄 Tipo de cambio: 1 USDT = {datos.tipo_cambio_usdt_ars.toFixed(2)} ARS ({datos.fuente_tipo_cambio})
        </p>

        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-sky-300">
            💎 En criptomoneda (USDT)
          </p>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <EstadisticaCapital
              etiqueta="Depositado"
              valor={formatearMoneda(datos.capital_inicial_usdt, "USDT", 4)}
            />
            <EstadisticaCapital
              etiqueta="Balance actual"
              valor={formatearMoneda(datos.balance_actual_usdt, "USDT", 4)}
            />
            <EstadisticaCapital
              etiqueta="Profit total"
              valor={
                <span className={claseColorGanancia(datos.profit_loss_usdt)}>
                  {datos.profit_loss_usdt >= 0 ? "+" : ""}
                  {formatearMoneda(datos.profit_loss_usdt, "USDT", 4)}
                </span>
              }
            />
          </div>
        </div>

        <p className="text-xs text-zinc-500">Actualizado {formatearHaceCuanto(datos.timestamp)}</p>
      </div>
    </Tarjeta>
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
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
      <Tarjeta titulo="Ganancia / Pérdida total">
        <p className={`text-3xl font-bold ${claseColorGanancia(datos.gananciaTotal)}`}>
          {formatearUSD(datos.gananciaTotal)}
        </p>
        <p className="mt-1 text-xs text-zinc-500">{datos.totalCerrados} operaciones cerradas</p>
      </Tarjeta>

      <Tarjeta titulo="Operaciones">
        <p className="text-sm text-zinc-300">
          Abiertas: <span className="font-semibold text-emerald-400">{datos.totalAbiertos}</span>
        </p>
        <p className="mt-1 text-sm text-zinc-300">
          Cerradas: <span className="font-semibold text-zinc-100">{datos.totalCerrados}</span>
        </p>
      </Tarjeta>

      <Tarjeta titulo="Win rate">
        <p className="text-3xl font-bold text-zinc-100">{datos.winRate.toFixed(1)}%</p>
        <p className="mt-1 text-xs text-zinc-500">Solo operaciones cerradas</p>
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

function OperacionesAbiertas({ operaciones }) {
  const hayOperaciones = operaciones && operaciones.length > 0;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900/60 p-5 shadow-sm">
      <h2 className="mb-4 text-sm font-medium text-zinc-400">Operaciones abiertas</h2>

      {!hayOperaciones ? (
        <p className="text-sm text-zinc-500">Sin operaciones abiertas en este momento.</p>
      ) : (
        <div className="space-y-3">
          {operaciones.map((op) => {
            const profitConocido = op.profitActualPct !== null && op.profitActualPct !== undefined;
            return (
              <div
                key={op.id}
                className="flex flex-col gap-1 rounded-lg border border-zinc-800/80 bg-zinc-950/40 px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
              >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                  <span className="inline-flex items-center gap-1 font-medium text-emerald-400">
                    🟢 ABIERTO
                  </span>
                  <span className="text-zinc-500">·</span>
                  <span className="font-medium text-zinc-100">{op.pair}</span>
                  <span className="text-zinc-500">·</span>
                  <span className="text-zinc-300">Entrada: {formatearUSD(op.entry_price)}</span>
                  <span className="text-zinc-500">·</span>
                  <span className={profitConocido ? claseColorGanancia(op.profitActualPct) : "text-zinc-500"}>
                    Profit actual: {profitConocido ? `${op.profitActualPct.toFixed(2)}%` : "sin datos"}
                  </span>
                </div>
                <div className="text-xs text-zinc-500">⏱️ Abierta hace {op.minutosAbierto} min</div>
              </div>
            );
          })}
        </div>
      )}
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
        <div className="h-56 w-full overflow-hidden">
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
                <th className="py-2 pr-4 font-medium">Estado</th>
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
                  <td className="py-2 pr-4">
                    <span className="inline-flex items-center gap-1 whitespace-nowrap text-emerald-400">
                      ✅ CERRADO
                    </span>
                  </td>
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
