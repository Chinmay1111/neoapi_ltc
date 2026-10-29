"""
websocket.py - Kotak Neo API Live SFeed WebSocket Market Data Streamer

Streams real-time live market feed for Nifty Futures (or any specified scrip)
with a clean, professional terminal UI matching the exact Kotak Neo SFeed layout.

UI Layout:
[*] Querying Scrip Master for symbol='NIFTY' on segment='nse_fo'...

┌────────────────────────────────────────────────────────────┐
│        KOTAK NEO SFEED WEBSOCKET - LIVE MARKET DATA        │
├────────────────────────────────────────────────────────────┤
│ Trading Symbol  : NIFTY26NOVFUT   Segment   : nse_fo       │
│ Instrument Token: 61471           Inst Type : FUTIDX       │
│ Expiry Date     : 26Nov2026       Market Lot: 65           │
└────────────────────────────────────────────────────────────┘

[*] Streaming live ticks (Press Ctrl+C to disconnect safely)...

  TIME   |   LTP (₹)   |      CHANGE      |   HIGH (₹)   |   LOW (₹)    | OPEN INTEREST
──────────────────────────────────────────────────────────────────────────────────────────
 09:15:02 | ₹ 24,525.50 |  +25.50 (+0.10%) |  24,540.00   |   24,495.00  |    16,050,000
"""

import argparse
import asyncio
import contextlib
import datetime
import io
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Enable ANSI colors & UTF-8 in Windows Command Prompt
if sys.platform == "win32":
    os.system("")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Silence noisy SDK background logger output so it doesn't break the table
logging.getLogger("neo_api_client").setLevel(logging.ERROR)
logging.getLogger("websockets").setLevel(logging.ERROR)
try:
    import structlog
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
except Exception:
    pass

# Ensure root directory is in sys.path
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    import pandas as pd
    from neo_api_client import NeoAPI
    from neo_api_client.utils import scrip_cache
    from neo_api_client.websocket.feed import SFeedScrip, SFeedWebSocket, WsToken
except ImportError as err:
    print(
        f"\n[ERROR] Failed to import required packages: {err}\n"
        "Please ensure requirements are installed via:\n"
        "    pip install -r requirements.txt\n"
    )
    sys.exit(1)

from login import Colors, load_env_file, login


# =====================================================================
# Silent Authentication Helper
# =====================================================================

def silent_login(verbose: bool = False) -> NeoAPI:
    """
    Authenticate with Kotak Neo.
    If verbose is False and credentials exist in .env, suppresses login banners
    so the terminal opens cleanly into the live market table.
    """
    load_env_file()
    has_env = all(
        os.environ.get(k)
        for k in ["NEO_CONSUMER_KEY", "NEO_MOBILE_NUMBER", "NEO_UCC", "NEO_MPIN"]
    )

    if not verbose and has_env:
        # Suppress login banners to keep terminal output clean
        devnull = io.StringIO()
        with contextlib.redirect_stdout(devnull):
            client, _ = login(show_limits=False)
        return client
    else:
        client, _ = login(show_limits=False)
        return client


# =====================================================================
# Scrip Master & Instrument Token Resolver
# =====================================================================

def _load_scrip_dataframe_from_cache(segment: str = "nse_fo") -> Optional[pd.DataFrame]:
    """Load cached master scrip CSV from ~/.kotak_neo/scrip_cache/."""
    try:
        cache_dir = scrip_cache._cache_dir()
        cached_files = sorted(cache_dir.glob(f"{segment}_*.csv"), reverse=True)
        if cached_files:
            latest_file = cached_files[0]
            df = pd.read_csv(latest_file, low_memory=False)
            df = df.rename(columns=lambda x: str(x).strip())
            return df
    except Exception:
        pass
    return None


def get_nifty_future_scrip(
    client: Optional[NeoAPI] = None,
    symbol: str = "NIFTY",
    expiry_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Search Kotak Neo scrip master (nse_fo) to resolve contract details
    for NIFTY Futures (FUTIDX).
    """
    print(f"[*] Querying Scrip Master for symbol='{symbol}' on segment='nse_fo'...\n")

    exact_futs: List[Dict[str, Any]] = []

    # 1. Official client.search_scrip()
    if client is not None:
        try:
            scrips = client.search_scrip(
                exchange_segment="nse_fo",
                symbol=symbol.lower(),
                option_type="FUT",
            )
            if isinstance(scrips, list) and scrips:
                for s in scrips:
                    if (
                        str(s.get("pSymbolName", "")).strip().upper() == symbol.upper()
                        and str(s.get("pInstType", "")).strip().upper() == "FUTIDX"
                    ):
                        exact_futs.append(s)
        except Exception:
            pass

    # 2. Local cache CSV (~/.kotak_neo/scrip_cache/nse_fo_*.csv)
    if not exact_futs:
        df = _load_scrip_dataframe_from_cache("nse_fo")
        if df is not None and not df.empty:
            sym_col = "pSymbolName" if "pSymbolName" in df.columns else None
            type_col = "pInstType" if "pInstType" in df.columns else None
            
            if sym_col and type_col:
                mask = (df[sym_col].astype(str).str.strip().str.upper() == symbol.upper()) & (
                    df[type_col].astype(str).str.strip().str.upper() == "FUTIDX"
                )
                filtered_df = df[mask].copy()
                if not filtered_df.empty:
                    if "pExpiryDate" in filtered_df.columns:
                        try:
                            dt = pd.to_datetime(pd.to_numeric(filtered_df["pExpiryDate"], errors="coerce"), unit="s")
                            dt = dt + pd.to_timedelta(315511200, unit="s")
                            filtered_df["pExpiryDateStr"] = dt.dt.strftime("%d%b%Y")
                        except Exception:
                            filtered_df["pExpiryDateStr"] = filtered_df["pExpiryDate"].astype(str)
                    records = filtered_df.to_dict(orient="records")
                    for r in records:
                        if "pExpiryDateStr" in r and r["pExpiryDateStr"]:
                            r["pExpiryDate"] = r["pExpiryDateStr"]
                        exact_futs.append(r)

    # 3. Known active fallback if off-hours network fails
    if not exact_futs and symbol.upper() == "NIFTY":
        return {
            "instrument_token": "61471",
            "trading_symbol": "NIFTY26NOVFUT",
            "expiry_date": "26Nov2026",
            "lot_size": 65,
            "tick_size": 0.05,
            "segment": "nse_fo",
            "inst_type": "FUTIDX",
        }

    if not exact_futs:
        raise ValueError(f"Could not resolve active {symbol} futures contracts in nse_fo.")

    # Sort chronologically by lExpiryDate
    exact_futs.sort(key=lambda s: int(s.get("lExpiryDate", 0) or 0))

    chosen_scrip: Optional[Dict[str, Any]] = None

    if expiry_hint:
        hint_clean = expiry_hint.strip().upper().replace("TH", "").replace(" ", "")
        for s in exact_futs:
            trd_sym = str(s.get("pTrdSymbol", "")).upper()
            exp_date = str(s.get("pExpiryDate", "")).upper()
            if hint_clean in trd_sym or hint_clean in exp_date:
                chosen_scrip = s
                break

    # Default to the first (current month / near-month) active future
    if not chosen_scrip:
        chosen_scrip = exact_futs[0]

    token = str(chosen_scrip.get("pSymbol"))
    trd_sym = str(chosen_scrip.get("pTrdSymbol"))
    expiry_str = str(chosen_scrip.get("pExpiryDate"))
    lot_size = int(chosen_scrip.get("lLotSize") or chosen_scrip.get("iLotSize") or 65)
    tick_size = float(chosen_scrip.get("dTickSize") or 10) / 100.0 if chosen_scrip.get("dTickSize") else 0.05

    return {
        "instrument_token": token,
        "trading_symbol": trd_sym,
        "expiry_date": expiry_str,
        "lot_size": lot_size,
        "tick_size": tick_size,
        "segment": "nse_fo",
        "inst_type": "FUTIDX",
        "raw": chosen_scrip,
    }


# =====================================================================
# Boxed Contract Card & Table Formatter
# =====================================================================

def display_contract_box(info: Dict[str, Any]) -> None:
    """Print the exact boxed contract summary card shown in the screenshot."""
    w = 60
    title = "KOTAK NEO SFEED WEBSOCKET - LIVE MARKET DATA"
    top = "┌" + "─" * w + "┐"
    mid = "├" + "─" * w + "┤"
    bot = "└" + "─" * w + "┘"

    r1 = " Trading Symbol  : " + str(info["trading_symbol"]).ljust(16) + "Segment   : " + str(info["segment"]).ljust(10)
    r2 = " Instrument Token: " + str(info["instrument_token"]).ljust(16) + "Inst Type : " + str(info.get("inst_type", "FUTIDX")).ljust(10)
    r3 = " Expiry Date     : " + str(info["expiry_date"]).ljust(16) + "Market Lot: " + str(info["lot_size"]).ljust(10)

    # Print in bright cyan color
    cyan = Colors.CYAN
    reset = Colors.RESET

    print(f"{cyan}{top}")
    print(f"│{title.center(w)}│")
    print(f"{mid}")
    print(f"│{r1.ljust(w)}│")
    print(f"│{r2.ljust(w)}│")
    print(f"│{r3.ljust(w)}│")
    print(f"{bot}{reset}\n")


def print_table_header() -> None:
    """Print the table column header and divider line."""
    print("[*] Streaming live ticks (Press Ctrl+C to disconnect safely)...\n")
    header = "  TIME   |   LTP (₹)   |      CHANGE      |   HIGH (₹)   |   LOW (₹)    | OPEN INTEREST"
    divider = "─" * 90
    print(f"{Colors.BOLD}{header}{Colors.RESET}")
    print(divider)


def format_table_row(msg: SFeedScrip) -> str:
    """Format an incoming market data tick as a table row."""
    # Timestamp
    if msg.last_trade_time and msg.last_trade_time > 0:
        time_str = datetime.datetime.fromtimestamp(msg.last_trade_time).strftime("%H:%M:%S")
    else:
        time_str = datetime.datetime.now().strftime("%H:%M:%S")

    ltp = msg.last_traded_price
    net_chg = msg.net_change
    pct_chg = msg.net_change_percent
    high = msg.high_price
    low = msg.low_price
    oi = msg.open_interest

    # Determine color
    if net_chg < 0:
        color = Colors.RED
        chg_text = f"{net_chg:.2f} ({pct_chg:.2f}%)"
    elif net_chg > 0:
        color = Colors.GREEN
        chg_text = f"+{net_chg:.2f} (+{pct_chg:.2f}%)"
    else:
        color = Colors.RESET
        chg_text = f"0.00 (0.00%)"

    # Pad raw text to fixed widths before applying color codes
    ltp_text = f"₹ {ltp:>9,.2f}"
    chg_padded = f"{chg_text:>16}"

    ltp_display = f"{color}{Colors.BOLD}{ltp_text}{Colors.RESET}"
    chg_display = f"{color}{chg_padded}{Colors.RESET}"

    row = (
        f" {time_str} | {ltp_display} | {chg_display} | "
        f" {high:>10,.2f}  |  {low:>10,.2f}  | {oi:>13,}"
    )
    return row


# =====================================================================
# WebSocket Streaming Engine
# =====================================================================

async def stream_live_quotes(
    client: NeoAPI,
    instrument_token: str,
    exchange_segment: str = "nse_fo",
    trading_symbol: Optional[str] = None,
    max_ticks: Optional[int] = None,
) -> None:
    """Connect to SFeed WebSocket and stream live ticks into the table."""
    ws_token = WsToken(exchange_segment=exchange_segment, instrument_token=str(instrument_token))
    tick_count = 0

    try:
        async with client.create_websocket() as ws:
            # Subscribe to touch-line feed
            await ws.subscribe_scrips([ws_token])

            async for message in ws:
                if isinstance(message, SFeedScrip) or getattr(message, "type", "") == "scrip":
                    if str(message.instrument_token) == str(instrument_token):
                        tick_count += 1
                        row_line = format_table_row(message)
                        print(row_line)

                        if max_ticks and tick_count >= max_ticks:
                            break

    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n{Colors.RED}[X] Stream disconnected: {e}{Colors.RESET}")


# =====================================================================
# CLI Entry Point
# =====================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kotak Neo API Live SFeed WebSocket Market Data Streamer.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "-s", "--symbol",
        type=str,
        default="NIFTY",
        help="Underlying symbol (e.g. NIFTY, BANKNIFTY)",
    )
    parser.add_argument(
        "-e", "--expiry",
        type=str,
        default=None,
        help="Optional expiry filter or hint (e.g. 'NOV', 'OCT')",
    )
    parser.add_argument(
        "-t", "--token",
        type=str,
        default=None,
        help="Direct instrument token override (e.g. 61471).",
    )
    parser.add_argument(
        "-c", "--count",
        type=int,
        default=None,
        help="Stop after receiving N ticks (useful for automated testing).",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show verbose login details before the table.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Step 1: Quiet Authentication
    try:
        client = silent_login(verbose=args.verbose)
    except Exception as e:
        print(f"\n{Colors.RED}[X] Authentication failed: {e}{Colors.RESET}")
        sys.exit(1)

    # Step 2: Query Scrip Master
    if args.token:
        print(f"[*] Querying Scrip Master for symbol='{args.symbol}' on segment='nse_fo'...\n")
        scrip_info = {
            "instrument_token": str(args.token),
            "trading_symbol": f"{args.symbol.upper()}-{args.token}",
            "expiry_date": "Manual Override",
            "lot_size": 65,
            "tick_size": 0.05,
            "segment": "nse_fo",
            "inst_type": "FUTIDX",
        }
    else:
        try:
            scrip_info = get_nifty_future_scrip(
                client=client,
                symbol=args.symbol,
                expiry_hint=args.expiry,
            )
        except Exception as scrip_err:
            print(f"{Colors.RED}[X] Scrip resolution error: {scrip_err}{Colors.RESET}")
            sys.exit(1)

    # Step 3: Print Boxed Contract Card
    display_contract_box(scrip_info)

    # Step 4: Print Table Header
    print_table_header()

    # Step 5: Start Live Stream Loop
    try:
        asyncio.run(
            stream_live_quotes(
                client=client,
                instrument_token=scrip_info["instrument_token"],
                exchange_segment=scrip_info["segment"],
                trading_symbol=scrip_info["trading_symbol"],
                max_ticks=args.count,
            )
        )
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] Streaming stopped. Goodbye!{Colors.RESET}\n")
    except Exception as exc:
        print(f"\n{Colors.RED}[X] Streaming error: {exc}{Colors.RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
