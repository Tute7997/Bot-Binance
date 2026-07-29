# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository overview

This repo (`Bot-Binance` on GitHub) hosts two independent Python trading bots and a shared Next.js dashboard that only displays one of them. They share the same Supabase project and Telegram bot but otherwise don't import from each other.

## Commands

### Binance Testnet bot (repo root — play money)
```bash
pip install -r requirements.txt
python main.py
```

### Kraken bot (`Bot Kraken/` — real money)
```bash
pip install -r "Bot Kraken/requirements.txt"
python "Bot Kraken/kraken-bot.py"
```
Stop with Ctrl+C, not `kill`/closing the terminal — a clean Ctrl+C is what lets the bot try to close its Supabase session correctly (see below).

### Dashboard (`dashboard/`)
```bash
cd dashboard
npm install
npm run dev     # http://localhost:3000
npm run build
npm run lint
```
There's no automated test suite for the Python bots. `test_buy_sell.py` (repo root) is the closest thing to an integration check — it simulates one buy/sell cycle to confirm the Supabase + Telegram wiring, but it sends real Telegram messages and writes a real Supabase row, so run it deliberately, not as part of routine verification.

## Architecture

### Two bots, one dashboard
- **`main.py`** — Binance **Testnet** bot (RSI 14 + MACD 12/26/9, TP +5% / SL -3%), trades BTCUSDT/ETHUSDT with Binance's play money. Logs every trade to Supabase tables `trades` and `bot_heartbeat`, sends Telegram alerts, and persists open positions to `posiciones.json` so a restart doesn't lose track of them.
- **`Bot Kraken/kraken-bot.py`** — same RSI/MACD/TP/SL strategy, but trading **real money** on Kraken (`XXBTZUSD`/`XETHZUSD`). Kraken's `/OHLC` endpoint returns real historical candles (unlike some other exchanges explored during development), so it reuses the exact same indicator math as `main.py` without needing synthetic price history.
  - **Session system**: capital isn't a fixed config value — on startup the bot either adopts the currently-active session from Supabase (`account_state.session_activa`) or opens a new one using the live Kraken balance as `capital_inicial` (tables `sessions` + `account_state`). On a clean Ctrl+C it tries to close that session (realized profit gets folded into `account_state.capital_depositado`) **only if no position is currently open** — if one is open, the session is deliberately left active so the next run picks up where it left off.
  - **`KRAKEN_MODO_SIMULACION`** (env var, defaults to `true`) is the real/paper toggle: when true, the bot logs and Telegrams what it *would* trade but never calls Kraken's order endpoint. Flip it to `false` deliberately.
  - Logs to Supabase tables `trades_kraken` and `kraken_heartbeat`.
- **`dashboard/`** — Next.js (App Router). Single page (`app/page.js`) showing **only** the Kraken bot's current session, accumulated deposited capital, and session history — polls `GET /api/capital-kraken` every 5s. `POST /api/capital-kraken/start` and `/close` create/close sessions in Supabase directly from the UI.
  - **No process manager is involved.** Starting/closing a session from the dashboard only changes Supabase state — it does not restart `kraken-bot.py`. The bot only re-reads which session is active when it's manually stopped and started again.
  - A Testnet tab and, earlier, a tab for a third exchange (Ripio) both existed here at various points and were removed; `main.py` keeps running independently either way; it's just not surfaced in this dashboard anymore.

### Credentials
- Every bot folder (`Bot Binance/`, `Bot Kraken/`) has its own `.env.local` (gitignored) with that exchange's API keys plus the same shared `SUPABASE_URL`/`SUPABASE_KEY` and `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` — one Supabase project and one Telegram bot across everything.
- `dashboard/.env.local` holds server-only env vars (deliberately no `NEXT_PUBLIC_` prefix) so exchange keys never reach the browser bundle. All request signing happens server-side in `dashboard/lib/krakenSigned.js`, called only from Route Handlers under `dashboard/app/api/`.

### Kraken API specifics worth knowing before touching `Bot Kraken/`
- Auth is `API-Key` + `API-Sign` headers: `API-Sign = HMAC-SHA512(urlpath + SHA256(nonce + POST body), base64-decoded private key)`, base64-encoded. Implemented in both `Bot Kraken/kraken-bot.py` and `dashboard/lib/krakenSigned.js` — keep them in sync if the scheme ever needs to change.
- Legacy pair naming: BTC/ETH use `X`/`Z`-prefixed codes (`XXBTZUSD`, `XETHZUSD`); newer assets don't (e.g. Solana is `SOLUSD`, not `XSOLZUSD`).
- Kraken has no ARS-quoted pairs — capital/balance is tracked in USD (`ZUSD`) throughout.
