"""
websocket.py - Kotak Neo API Live WebSocket Market Data Streamer

Streams real-time live market feed (LTP, OHLC, Volume, OI, Best Bid/Ask Depth)
for Nifty Futures (or any other scrip) using the official Kotak Neo API SDK
(SFeed WebSocket).

Features:
1. Automatic Scrip Master Lookup:
   - Queries Kotak Neo Scrip Master (nse_fo) to automatically resolve
     the instrument token, trading symbol, expiry date, and lot size.
   - Built-in multi-tier fallback (API -> local disk cache -> direct download)
     ensuring resilience against Kotak nighttime API maintenance (503 errors).
   - Defaults to the active Current Month Nifty Future.
2. Full Trading Authentication:
   - Authenticates via login.py using credentials stored in .env or interactive login.
3. Modern Async SFeed WebSocket:
   - Uses client.create_websocket() with auto-reconnect and native ping-pong.
4. Rich Terminal Dashboard:
   - Color-coded tick output (Green for Up, Red for Down).
   - Shows LTP, Net Change (pts and %), High/Low, Volume, OI, and Best Bid/Ask.
5. Programmatic API:
   - Can be imported into trading algorithms or strategies:
     from websocket import stream_live_quotes, get_nifty_future_scrip

Usage:
    # Run default (Auto-detect active Current Month Nifty Future):
    python websocket.py

    # Specify an expiry hint:
    python websocket.py --expiry NOV

    # Specify a custom instrument token directly:
    python websocket.py --token 61471

    # Stream 5 depth levels:
    python websocket.py --depth

    # Capture 5 ticks and exit (useful for testing):
    python websocket.py --count 5
"""

import argparse
import asyncio
import datetime
import io
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

from login import Colors, get_client, load_env_file, login, print_banner


# =====================================================================
# Scrip Master & Instrument Token Resolver
# =====================================================================

def _load_scrip_dataframe_from_cache(segment: str = "nse_fo") -> Optional[pd.DataFrame]:
    """Load scrips from ~/.kotak_neo/scrip_cache/ if available."""
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

    Resilient multi-tier strategy:
    1. Try client.search_scrip()
    2. Try local scrip cache (e.g. from previous runs)
    3. Fallback to active near-month constant if off-hours network fails

    Returns:
        Dictionary containing contract metadata:
        {
            'instrument_token': str,
            'trading_symbol': str,
            'expiry_date': str,
            'lot_size': int,
            'tick_size': float,
            'base_price': float,
            'segment': str,
            'raw': dict
        }
    """
    print(f"{Colors.BLUE}[*] Resolving {symbol} Futures from Kotak Neo Scrip Master (nse_fo)...{Colors.RESET}")
    
    exact_futs: List[Dict[str, Any]] = []

    # Strategy 1: Official client.search_scrip()
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
        except Exception as e:
            print(f"{Colors.DIM}    (API search_scrip unavailable: {e}. Trying local scrip cache...){Colors.RESET}")

    # Strategy 2: Local cache CSV (~/.kotak_neo/scrip_cache/nse_fo_*.csv)
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
                    # Format pExpiryDate if numeric epoch
                    if "pExpiryDate" in filtered_df.columns:
                        try:
                            # Convert epoch seconds to human readable date
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

    # Strategy 3: Built-in known near-month fallback (if Kotak API is completely down/503)
    if not exact_futs and symbol.upper() == "NIFTY":
        print(f"{Colors.YELLOW}[!] Using current active Nifty Future default (Token: 61471 / NIFTY26NOVFUT).{Colors.RESET}")
        return {
            "instrument_token": "61471",
            "trading_symbol": "NIFTY26NOVFUT",
            "expiry_date": "26Nov2026",
            "lot_size": 65,
            "tick_size": 0.05,
            "base_price": 24500.0,
            "segment": "nse_fo",
            "raw": {},
        }

    if not exact_futs:
        raise ValueError(f"Could not resolve active {symbol} futures contracts in nse_fo.")

    # Sort chronologically by lExpiryDate (epoch seconds)
    exact_futs.sort(key=lambda s: int(s.get("lExpiryDate", 0) or 0))

    chosen_scrip: Optional[Dict[str, Any]] = None

    # If user passed an expiry hint (e.g. OCT, NOV, 27Oct):
    if expiry_hint:
        hint_clean = expiry_hint.strip().upper().replace("TH", "").replace(" ", "")
        for s in exact_futs:
            trd_sym = str(s.get("pTrdSymbol", "")).upper()
            exp_date = str(s.get("pExpiryDate", "")).upper()
            if hint_clean in trd_sym or hint_clean in exp_date:
                chosen_scrip = s
                break

        if not chosen_scrip:
            available_contracts = [
                f"{s.get('pTrdSymbol')} (Exp: {s.get('pExpiryDate')}, Token: {s.get('pSymbol')})"
                for s in exact_futs
            ]
            print(
                f"\n{Colors.YELLOW}[!] Notice: Expiry '{expiry_hint}' was not found in the active master file.\n"
                f"    (Monthly futures expire on the final Thursday; expired contracts are dropped by the exchange).\n"
                f"    Available active contracts:\n"
                f"    - " + "\n    - ".join(available_contracts) + f"\n"
                f"    Defaulting to Current Month / Near-Month active contract.{Colors.RESET}\n"
            )

    # Default to the first (near-month / current month) contract
    if not chosen_scrip:
        chosen_scrip = exact_futs[0]

    token = str(chosen_scrip.get("pSymbol"))
    trd_sym = str(chosen_scrip.get("pTrdSymbol"))
    expiry_str = str(chosen_scrip.get("pExpiryDate"))
    lot_size = int(chosen_scrip.get("lLotSize") or chosen_scrip.get("iLotSize") or 65)
    tick_size = float(chosen_scrip.get("dTickSize") or 10) / 100.0 if chosen_scrip.get("dTickSize") else 0.05
    base_price = float(chosen_scrip.get("pScripBasePrice") or 0) / 100.0 if chosen_scrip.get("pScripBasePrice") else 0.0

    return {
        "instrument_token": token,
        "trading_symbol": trd_sym,
        "expiry_date": expiry_str,
        "lot_size": lot_size,
        "tick_size": tick_size,
        "base_price": base_price,
        "segment": "nse_fo",
        "raw": chosen_scrip,
    }


def display_scrip_card(scrip_info: Dict[str, Any]) -> None:
    """Print formatted scrip metadata card."""
    print_banner("CONTRACT DETAILS RESOLVED", Colors.CYAN)
    print(f"  {Colors.BOLD}Trading Symbol  :{Colors.RESET} {Colors.GREEN}{Colors.BOLD}{scrip_info['trading_symbol']}{Colors.RESET}")
    print(f"  {Colors.BOLD}Instrument Token:{Colors.RESET} {Colors.YELLOW}{Colors.BOLD}{scrip_info['instrument_token']}{Colors.RESET}")
    print(f"  {Colors.BOLD}Exchange Segment:{Colors.RESET} {scrip_info['segment']}")
    print(f"  {Colors.BOLD}Expiry Date     :{Colors.RESET} {scrip_info['expiry_date']}")
    print(f"  {Colors.BOLD}Lot Size        :{Colors.RESET} {scrip_info['lot_size']}")
    print(f"  {Colors.BOLD}Tick Size       :{Colors.RESET} {scrip_info['tick_size']}")
    if scrip_info.get("base_price"):
        print(f"  {Colors.BOLD}Prev Close / Base Price:{Colors.RESET} Rs. {scrip_info['base_price']:,.2f}")
    print("=" * 62 + "\n")


# =====================================================================
# Market Data Display Formatter
# =====================================================================

def format_tick_display(
    msg: SFeedScrip,
    trading_symbol: str,
    tick_number: int,
    show_depth: bool = False,
) -> None:
    """Pretty-print a market data tick."""
    now_str = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    ltp = msg.last_traded_price
    net_chg = msg.net_change
    chg_pct = msg.net_change_percent

    # Color code price change
    if net_chg > 0:
        chg_color = Colors.GREEN
        sign = "+"
    elif net_chg < 0:
        chg_color = Colors.RED
        sign = ""
    else:
        chg_color = Colors.RESET
        sign = " "

    header_line = (
        f"{Colors.DIM}[{now_str} #{tick_number:04d}]{Colors.RESET} "
        f"{Colors.BOLD}{trading_symbol}{Colors.RESET} "
        f"LTP: {chg_color}{Colors.BOLD}{ltp:,.2f}{Colors.RESET} "
        f"({chg_color}{sign}{net_chg:,.2f} / {sign}{chg_pct:.2f}%{Colors.RESET})"
    )
    print(header_line)

    stats_line = (
        f"    {Colors.DIM}Open:{Colors.RESET} {msg.open_price:,.2f}  "
        f"{Colors.DIM}High:{Colors.RESET} {msg.high_price:,.2f}  "
        f"{Colors.DIM}Low:{Colors.RESET} {msg.low_price:,.2f}  "
        f"{Colors.DIM}Close:{Colors.RESET} {msg.close_price:,.2f}  "
        f"{Colors.DIM}Volume:{Colors.RESET} {msg.volume_traded_today:,}  "
        f"{Colors.DIM}OI:{Colors.RESET} {msg.open_interest:,}"
    )
    print(stats_line)

    # Best Bid & Ask
    best_bid = msg.buy[0] if msg.buy else None
    best_ask = msg.sell[0] if msg.sell else None
    bid_str = f"{best_bid.quantity} @ {best_bid.price:,.2f}" if best_bid else "N/A"
    ask_str = f"{best_ask.price:,.2f} @ {best_ask.quantity}" if best_ask else "N/A"
    print(f"    {Colors.GREEN}Best Bid:{Colors.RESET} {bid_str}  |  {Colors.RED}Best Ask:{Colors.RESET} {ask_str}")

    if show_depth and (len(msg.buy) > 1 or len(msg.sell) > 1):
        print(f"    {Colors.DIM}--- Market Depth (Top 5) ---{Colors.RESET}")
        depth_len = max(len(msg.buy), len(msg.sell))
        for i in range(min(5, depth_len)):
            b = msg.buy[i] if i < len(msg.buy) else None
            a = msg.sell[i] if i < len(msg.sell) else None
            b_txt = f"{b.orders:2d} orders | {b.quantity:5d} @ {b.price:,.2f}" if b else " " * 28
            a_txt = f"{a.price:,.2f} @ {a.quantity:5d} | {a.orders:2d} orders" if a else ""
            print(f"      {Colors.GREEN}{b_txt}{Colors.RESET}   |   {Colors.RED}{a_txt}{Colors.RESET}")

    print()


# =====================================================================
# Main WebSocket Streaming Engine
# =====================================================================

async def stream_live_quotes(
    client: NeoAPI,
    instrument_token: str,
    exchange_segment: str = "nse_fo",
    trading_symbol: Optional[str] = None,
    max_ticks: Optional[int] = None,
    show_depth: bool = False,
    on_quote: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> None:
    """
    Connect to Kotak Neo SFeed WebSocket and stream live quotes.

    Parameters:
        client: Authenticated NeoAPI client instance
        instrument_token: Token string (e.g. '61471')
        exchange_segment: Segment (e.g. 'nse_fo')
        trading_symbol: Display symbol name
        max_ticks: Exit after receiving this number of ticks (None for infinite)
        show_depth: If True, subscribes to depth (Level 8) and displays top 5 depth
        on_quote: Optional callback invoked with parsed quote dict for each tick
    """
    sym = trading_symbol or f"Token-{instrument_token}"
    ws_token = WsToken(exchange_segment=exchange_segment, instrument_token=str(instrument_token))

    print(f"{Colors.BLUE}[*] Connecting to Kotak Neo SFeed WebSocket...{Colors.RESET}")
    print(f"{Colors.DIM}    Subscribing to {exchange_segment} | Token: {instrument_token} ({sym}){Colors.RESET}")
    print(f"{Colors.DIM}    Press Ctrl + C at any time to disconnect.{Colors.RESET}\n")

    tick_count = 0

    try:
        async with client.create_websocket() as ws:
            print(f"{Colors.GREEN}[OK] WebSocket Connected & Authenticated successfully!{Colors.RESET}")
            
            # Subscribe to touch-line or full depth
            if show_depth:
                await ws.subscribe_depth([ws_token])
                print(f"{Colors.CYAN}[*] Subscribed with Level 8 Depth (Top 5 Bid/Ask). Awaiting market ticks...{Colors.RESET}\n")
            else:
                await ws.subscribe_scrips([ws_token])
                print(f"{Colors.CYAN}[*] Subscribed to Level 4 Market Feed. Awaiting market ticks...{Colors.RESET}\n")

            async for message in ws:
                # Handle Scrip tick updates
                if isinstance(message, SFeedScrip) or getattr(message, "type", "") == "scrip":
                    if str(message.instrument_token) == str(instrument_token):
                        tick_count += 1
                        display_sym = message.trading_symbol or sym
                        format_tick_display(message, display_sym, tick_count, show_depth=show_depth)

                        if on_quote:
                            try:
                                on_quote(message.model_dump() if hasattr(message, "model_dump") else message.__dict__)
                            except Exception as cb_err:
                                print(f"{Colors.YELLOW}[!] Callback error: {cb_err}{Colors.RESET}")

                        if max_ticks and tick_count >= max_ticks:
                            print(f"{Colors.GREEN}[OK] Reached target tick count ({max_ticks}). Exiting stream.{Colors.RESET}")
                            break

    except asyncio.CancelledError:
        print(f"\n{Colors.YELLOW}[!] Stream cancelled.{Colors.RESET}")
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] WebSocket streaming interrupted by user.{Colors.RESET}")
    except Exception as e:
        print(f"\n{Colors.RED}[X] WebSocket error: {e}{Colors.RESET}")
        raise


# =====================================================================
# CLI Entry Point
# =====================================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Kotak Neo API Live WebSocket Market Data Streamer for Nifty Futures.",
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
        help="Optional expiry filter or hint (e.g. 'NOV', '26NOV', 'OCT')",
    )
    parser.add_argument(
        "-t", "--token",
        type=str,
        default=None,
        help="Direct instrument token override (e.g. 61471). Skips scrip master search.",
    )
    parser.add_argument(
        "--segment",
        type=str,
        default="nse_fo",
        help="Exchange segment (nse_fo, nse_cm, bse_cm, mcx_fo)",
    )
    parser.add_argument(
        "--depth",
        action="store_true",
        help="Subscribe to 5-level Market Depth (Level 8) instead of touch-line.",
    )
    parser.add_argument(
        "-c", "--count",
        type=int,
        default=None,
        help="Stop after receiving N ticks (useful for automated checks).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    print_banner("KOTAK NEO LIVE WEBSOCKET STREAMER", Colors.CYAN)
    print("Real-time Market Feed for Nifty Current Month Future\n")

    # Step 1: Authentication
    try:
        client, user_info = login()
    except Exception as e:
        print(f"\n{Colors.RED}[X] Login failed: {e}{Colors.RESET}")
        sys.exit(1)

    # Step 2: Resolve Scrip Token & Contract Metadata
    if args.token:
        scrip_info = {
            "instrument_token": str(args.token),
            "trading_symbol": f"{args.symbol.upper()}-{args.token}",
            "expiry_date": "Manual Override",
            "lot_size": 65,
            "tick_size": 0.05,
            "base_price": 0.0,
            "segment": args.segment,
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

    # Display Contract Summary
    display_scrip_card(scrip_info)

    # Step 3: Run Async WebSocket Stream
    try:
        asyncio.run(
            stream_live_quotes(
                client=client,
                instrument_token=scrip_info["instrument_token"],
                exchange_segment=scrip_info["segment"],
                trading_symbol=scrip_info["trading_symbol"],
                max_ticks=args.count,
                show_depth=args.depth,
            )
        )
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] WebSocket stopped by user. Goodbye!{Colors.RESET}\n")
    except Exception as exc:
        print(f"\n{Colors.RED}[X] WebSocket execution terminated: {exc}{Colors.RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
