"""
orderplacement.py - Kotak Neo Broker Order Placement Script

This script places orders with Kotak Neo broker using the official
Kotak Neo API SDK (neo_api_client).

Supported Usage:
1. Command Line Interface (CLI):
   python orderplacement.py --symbol RELIANCE-EQ --action BUY --qty 1 --price 2950.50 --type LIMIT --exchange nse_cm --product CNC
   python orderplacement.py -s INFY-EQ -a BUY -q 10 -t MKT -y

2. Interactive Guided Wizard (run without arguments):
   python orderplacement.py

3. Programmatic API (import in your Python trading bot/strategy):
   from orderplacement import place_order
   res = place_order(
       trading_symbol="RELIANCE-EQ",
       transaction_type="BUY",
       quantity=1,
       order_type="LIMIT",
       price=2950.50,
       exchange_segment="nse_cm",
       product="CNC",
   )
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

# Enable ANSI colors & UTF-8 in Windows console
if sys.platform == "win32":
    os.system("")
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Ensure root directory is in sys.path so neo_api_client is discoverable
CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

try:
    from neo_api_client import NeoAPI
except ImportError as err:
    print(
        f"\n[ERROR] Failed to import 'neo_api_client': {err}\n"
        "Please ensure the SDK is available in the project directory or installed via:\n"
        "    pip install kotakneoapi\n"
    )
    sys.exit(1)

from login import Colors, get_client, login, print_banner


# =====================================================================
# Constants & Allowed Values (Kotak Neo Canonical Codes)
# =====================================================================

ALLOWED_EXCHANGE_SEGMENTS = [
    "nse_cm",   # NSE Equity Cash
    "bse_cm",   # BSE Equity Cash
    "nse_fo",   # NSE Futures & Options
    "bse_fo",   # BSE Futures & Options
    "mcx_fo",   # MCX Commodity F&O
    "nse_com",  # NSE Commodity
]

SEGMENT_DESCRIPTIONS = {
    "nse_cm": "NSE Cash / Equities",
    "bse_cm": "BSE Cash / Equities",
    "nse_fo": "NSE Futures & Options",
    "bse_fo": "BSE Futures & Options",
    "mcx_fo": "MCX Commodities (F&O)",
    "nse_com": "NSE Commodities",
}

EXCHANGE_SEGMENT_ALIASES = {
    "nse_cm": "nse_cm",
    "nse": "nse_cm",
    "nsecm": "nse_cm",
    "nse-cm": "nse_cm",
    "cm": "nse_cm",
    "cash": "nse_cm",
    "bse_cm": "bse_cm",
    "bse": "bse_cm",
    "bsecm": "bse_cm",
    "bse-cm": "bse_cm",
    "nse_fo": "nse_fo",
    "nfo": "nse_fo",
    "nsefo": "nse_fo",
    "nse-fo": "nse_fo",
    "fo": "nse_fo",
    "fno": "nse_fo",
    "bse_fo": "bse_fo",
    "bfo": "bse_fo",
    "bsefo": "bse_fo",
    "bse-fo": "bse_fo",
    "mcx_fo": "mcx_fo",
    "mcx": "mcx_fo",
    "mcxfo": "mcx_fo",
    "mcx-fo": "mcx_fo",
    "commodity": "mcx_fo",
    "commodities": "mcx_fo",
    "nse_com": "nse_com",
    "nsecom": "nse_com",
    "nse-com": "nse_com",
}

ALLOWED_ORDER_TYPES = ["L", "MKT", "SL", "SL-M"]

ORDER_TYPE_ALIASES = {
    "l": "L",
    "limit": "L",
    "mkt": "MKT",
    "market": "MKT",
    "sl": "SL",
    "stoploss": "SL",
    "stop_loss": "SL",
    "stop-loss": "SL",
    "sl-l": "SL",
    "sl_l": "SL",
    "sl-m": "SL-M",
    "slm": "SL-M",
    "stoploss_market": "SL-M",
    "stop_loss_market": "SL-M",
    "stoploss-market": "SL-M",
}

ORDER_TYPE_DESCRIPTIONS = {
    "L": "Limit Order (execute at specified limit price or better)",
    "MKT": "Market Order (execute at prevailing market price)",
    "SL": "Stop-Loss Limit Order (triggers at trigger price, executes as Limit)",
    "SL-M": "Stop-Loss Market Order (triggers at trigger price, executes as Market)",
}

ALLOWED_PRODUCTS = ["CNC", "MIS", "NRML", "MTF"]

PRODUCT_ALIASES = {
    "cnc": "CNC",
    "cash and carry": "CNC",
    "cash_and_carry": "CNC",
    "delivery": "CNC",
    "mis": "MIS",
    "intraday": "MIS",
    "intra-day": "MIS",
    "nrml": "NRML",
    "normal": "NRML",
    "carryforward": "NRML",
    "carry_forward": "NRML",
    "mtf": "MTF",
    "margin_trade_funding": "MTF",
}

PRODUCT_DESCRIPTIONS = {
    "CNC": "Cash & Carry (Equity Delivery / Investing)",
    "MIS": "Margin Intraday Square-off (Day Trading)",
    "NRML": "Normal (F&O overnight / carryforward position)",
    "MTF": "Margin Trade Funding (Pay margin, Kotak funds balance)",
}

TRANSACTION_TYPE_ALIASES = {
    "b": "B",
    "buy": "B",
    "long": "B",
    "s": "S",
    "sell": "S",
    "short": "S",
}

VALIDITY_ALIASES = {
    "day": "DAY",
    "ioc": "IOC",
}


# =====================================================================
# Normalization & Validation Functions
# =====================================================================

def normalize_symbol(symbol: str) -> str:
    """Normalize and validate trading symbol."""
    if not symbol or not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("Trading symbol is required and cannot be empty (e.g. 'RELIANCE-EQ').")
    return symbol.strip().upper()


def normalize_exchange_segment(exchange: str) -> str:
    """Normalize exchange segment to Kotak Neo canonical string."""
    if not exchange or not isinstance(exchange, str):
        raise ValueError(
            f"Exchange segment is required. Allowed values: {', '.join(ALLOWED_EXCHANGE_SEGMENTS)}"
        )
    cleaned = exchange.strip().lower()
    canonical = EXCHANGE_SEGMENT_ALIASES.get(cleaned)
    if not canonical or canonical not in ALLOWED_EXCHANGE_SEGMENTS:
        raise ValueError(
            f"Invalid exchange segment '{exchange}'. Allowed values: {', '.join(ALLOWED_EXCHANGE_SEGMENTS)}"
        )
    return canonical


def normalize_transaction_type(action: str) -> str:
    """Normalize transaction type (BUY/SELL) to 'B' or 'S'."""
    if not action or not isinstance(action, str):
        raise ValueError("Transaction type is required: 'BUY' ('B') or 'SELL' ('S').")
    cleaned = action.strip().lower()
    canonical = TRANSACTION_TYPE_ALIASES.get(cleaned)
    if not canonical:
        raise ValueError(f"Invalid transaction type '{action}'. Allowed: BUY (B) or SELL (S).")
    return canonical


def normalize_order_type(order_type: str) -> str:
    """Normalize order type to canonical code: L, MKT, SL, SL-M."""
    if not order_type or not isinstance(order_type, str):
        raise ValueError(f"Order type is required. Allowed: {', '.join(ALLOWED_ORDER_TYPES)}")
    cleaned = order_type.strip().lower()
    canonical = ORDER_TYPE_ALIASES.get(cleaned)
    if not canonical or canonical not in ALLOWED_ORDER_TYPES:
        raise ValueError(
            f"Invalid order type '{order_type}'. Allowed: {', '.join(ALLOWED_ORDER_TYPES)} (Limit, Market, SL, SL-M)"
        )
    return canonical


def normalize_product(product: str) -> str:
    """Normalize product type to canonical code: CNC, MIS, NRML, MTF."""
    if not product or not isinstance(product, str):
        raise ValueError(f"Product type is required. Allowed: {', '.join(ALLOWED_PRODUCTS)}")
    cleaned = product.strip().lower()
    canonical = PRODUCT_ALIASES.get(cleaned)
    if not canonical or canonical not in ALLOWED_PRODUCTS:
        raise ValueError(
            f"Invalid product '{product}'. Allowed: {', '.join(ALLOWED_PRODUCTS)} (CNC, MIS, NRML, MTF)"
        )
    return canonical


def normalize_validity(validity: str, exchange_segment: str = "nse_cm") -> str:
    """Normalize validity: DAY or IOC."""
    if not validity or not isinstance(validity, str):
        validity = "DAY"
    cleaned = validity.strip().lower()
    canonical = VALIDITY_ALIASES.get(cleaned)
    if not canonical or canonical not in ["DAY", "IOC"]:
        raise ValueError(f"Invalid validity '{validity}'. Allowed values: DAY, IOC.")
    if exchange_segment == "mcx_fo" and canonical == "IOC":
        raise ValueError("MCX F&O segment allows only 'DAY' validity, IOC is not supported.")
    return canonical


def normalize_quantity(qty: Union[int, str]) -> str:
    """Validate quantity is a positive integer string."""
    try:
        parsed = int(str(qty).strip())
    except (ValueError, TypeError):
        raise ValueError(f"Quantity must be a valid positive integer, got {qty!r}.")
    if parsed <= 0:
        raise ValueError(f"Quantity must be greater than zero, got {parsed}.")
    return str(parsed)


def normalize_price(price: Union[float, int, str, None], order_type: str) -> str:
    """
    Validate and format price as string.
    - For MKT and SL-M: price is always '0'.
    - For L and SL: price must be > 0.
    """
    if order_type in ["MKT", "SL-M"]:
        return "0"

    if price is None or str(price).strip() == "":
        raise ValueError(f"Price is mandatory for order type '{order_type}'.")

    try:
        parsed = float(str(price).strip())
    except (ValueError, TypeError):
        raise ValueError(f"Price must be a valid number, got {price!r}.")

    if parsed <= 0:
        raise ValueError(f"Price must be greater than zero for order type '{order_type}', got {parsed}.")

    # Format cleanly (2 decimal places)
    return f"{parsed:.2f}"


def normalize_trigger_price(
    trigger_price: Union[float, int, str, None], order_type: str
) -> str:
    """
    Validate and format trigger price.
    - For SL and SL-M: trigger_price must be > 0.
    - For L and MKT: defaults to '0'.
    """
    if order_type in ["SL", "SL-M"]:
        if trigger_price is None or str(trigger_price).strip() in ("", "0"):
            raise ValueError(
                f"Trigger price is mandatory and must be > 0 for Stop-Loss order type '{order_type}'."
            )
        try:
            parsed = float(str(trigger_price).strip())
        except (ValueError, TypeError):
            raise ValueError(f"Trigger price must be a valid number, got {trigger_price!r}.")
        if parsed <= 0:
            raise ValueError(f"Trigger price must be greater than zero, got {parsed}.")
        return f"{parsed:.2f}"

    return "0"


def normalize_disclosed_quantity(
    dq: Union[int, str, None], total_qty: str
) -> str:
    """Validate disclosed quantity (0 or <= total quantity)."""
    if dq is None or str(dq).strip() in ("", "0"):
        return "0"
    try:
        parsed_dq = int(str(dq).strip())
        parsed_total = int(total_qty)
    except (ValueError, TypeError):
        raise ValueError(f"Disclosed quantity must be a non-negative integer, got {dq!r}.")
    if parsed_dq < 0:
        raise ValueError("Disclosed quantity cannot be negative.")
    if parsed_dq > parsed_total:
        raise ValueError(
            f"Disclosed quantity ({parsed_dq}) cannot exceed total quantity ({parsed_total})."
        )
    return str(parsed_dq)


def normalize_amo(amo: Union[str, bool, None]) -> str:
    """Normalize AMO (After Market Order) flag to 'YES' or 'NO'."""
    if isinstance(amo, bool):
        return "YES" if amo else "NO"
    if isinstance(amo, str):
        cleaned = amo.strip().lower()
        if cleaned in ["yes", "y", "true", "1"]:
            return "YES"
    return "NO"


# =====================================================================
# Card and Display Formatting
# =====================================================================

def display_order_summary_card(
    symbol: str,
    exchange: str,
    action: str,
    product: str,
    order_type: str,
    quantity: str,
    price: str,
    trigger_price: str,
    validity: str,
    amo: str,
    disclosed_quantity: str = "0",
    tag: Optional[str] = None,
) -> None:
    """Pretty prints the order parameters before placement."""
    action_color = Colors.GREEN if action in ["B", "BUY"] else Colors.RED
    action_text = "BUY (LONG)" if action in ["B", "BUY"] else "SELL (SHORT)"

    width = 62
    border = "=" * width
    print(f"\n{Colors.CYAN}{border}")
    print(f" {'ORDER SPECIFICATION SUMMARY'.center(width - 2)} ")
    print(f"{border}{Colors.RESET}")
    print(f"  {Colors.BOLD}Trading Symbol  :{Colors.RESET} {Colors.YELLOW}{Colors.BOLD}{symbol}{Colors.RESET}")
    print(f"  {Colors.BOLD}Exchange Segment:{Colors.RESET} {exchange} ({SEGMENT_DESCRIPTIONS.get(exchange, exchange)})")
    print(f"  {Colors.BOLD}Transaction Type:{Colors.RESET} {action_color}{Colors.BOLD}{action_text}{Colors.RESET}")
    print(f"  {Colors.BOLD}Product Type    :{Colors.RESET} {product} ({PRODUCT_DESCRIPTIONS.get(product, product)})")
    print(f"  {Colors.BOLD}Order Type      :{Colors.RESET} {order_type} ({ORDER_TYPE_DESCRIPTIONS.get(order_type, order_type)})")
    print(f"  {Colors.BOLD}Quantity        :{Colors.RESET} {Colors.BOLD}{quantity}{Colors.RESET} shares/lots")

    price_display = f"Rs. {price}" if order_type in ["L", "SL"] else "MARKET (Prevailing price)"
    print(f"  {Colors.BOLD}Limit Price     :{Colors.RESET} {price_display}")

    if order_type in ["SL", "SL-M"]:
        print(f"  {Colors.BOLD}Trigger Price   :{Colors.RESET} Rs. {trigger_price}")

    print(f"  {Colors.BOLD}Validity        :{Colors.RESET} {validity}")
    if amo == "YES":
        print(f"  {Colors.BOLD}Order Session   :{Colors.RESET} {Colors.YELLOW}AMO (After Market Order){Colors.RESET}")
    else:
        print(f"  {Colors.BOLD}Order Session   :{Colors.RESET} Regular Market Hours")

    if disclosed_quantity != "0":
        print(f"  {Colors.BOLD}Disclosed Qty   :{Colors.RESET} {disclosed_quantity}")
    if tag:
        print(f"  {Colors.BOLD}Order Tag       :{Colors.RESET} {tag}")

    print(f"{Colors.CYAN}{border}{Colors.RESET}\n")


def extract_order_id(response: Any) -> Optional[str]:
    """Safely extracts the broker order ID (nOrdNo) from Kotak Neo response."""
    if not isinstance(response, dict):
        return None

    # Check top-level nOrdNo
    if response.get("nOrdNo"):
        return str(response["nOrdNo"])

    # Check nested data.nOrdNo
    data = response.get("data")
    if isinstance(data, dict) and data.get("nOrdNo"):
        return str(data["nOrdNo"])
    if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict) and data[0].get("nOrdNo"):
        return str(data[0]["nOrdNo"])

    # Check result key
    result = response.get("result")
    if isinstance(result, dict) and result.get("nOrdNo"):
        return str(result["nOrdNo"])
    if isinstance(result, str) and result.isdigit():
        return result

    return None


def extract_order_error(response: Any) -> Optional[str]:
    """Extract error details from Kotak Neo response dictionary."""
    if not isinstance(response, dict):
        return str(response)

    # {"Error": ...} or {"error": ...}
    for err_key in ["Error", "error", "errMsg", "Error Message", "message"]:
        if err_key in response:
            val = response[err_key]
            if isinstance(val, list) and len(val) > 0:
                first = val[0]
                if isinstance(first, dict):
                    code = first.get("code", "")
                    msg = first.get("message") or first.get("errMsg", str(first))
                    return f"[{code}] {msg}" if code else msg
                return str(first)
            return str(val)

    if response.get("stat") not in [None, "Ok", "ok", "SUCCESS", "success"]:
        return response.get("errMsg") or response.get("message") or f"Status: {response.get('stat')}"

    return None


# =====================================================================
# Core Order Placement Function
# =====================================================================

def place_order(
    trading_symbol: str,
    transaction_type: str,
    quantity: Union[int, str],
    price: Union[float, int, str] = "0",
    order_type: str = "L",
    exchange_segment: str = "nse_cm",
    product: str = "CNC",
    validity: str = "DAY",
    amo: Union[str, bool] = "NO",
    disclosed_quantity: Union[int, str] = "0",
    trigger_price: Union[float, int, str] = "0",
    tag: Optional[str] = None,
    client: Optional[NeoAPI] = None,
    check_status: bool = True,
    verbose: bool = True,
) -> Dict[str, Any]:
    """
    Place an order with Kotak Neo broker.

    Parameters:
        trading_symbol (str): Symbol e.g. "RELIANCE-EQ", "INFY-EQ", "NIFTY24DEC24000CE"
        transaction_type (str): "BUY" / "B" or "SELL" / "S"
        quantity (int or str): Number of shares / lots (> 0)
        price (float/int/str): Limit price. Required for LIMIT and SL orders. (0 for MKT)
        order_type (str): "L" (Limit), "MKT" (Market), "SL", or "SL-M"
        exchange_segment (str): "nse_cm", "bse_cm", "nse_fo", "bse_fo", "mcx_fo", "nse_com"
        product (str): "CNC", "MIS", "NRML", "MTF"
        validity (str): "DAY" or "IOC" (default: "DAY")
        amo (str or bool): After Market Order flag ("YES" or "NO", default: "NO")
        disclosed_quantity (int or str): Disclosed quantity (default: "0")
        trigger_price (float/int/str): Trigger price for Stop-Loss orders (default: "0")
        tag (str, optional): User-defined reference tag (up to 20 chars)
        client (NeoAPI, optional): Existing authenticated NeoAPI instance. If None,
                                   automatically authenticates via login.py / .env
        check_status (bool): If True, queries order_report to verify live status
        verbose (bool): Print progress messages and result cards to console

    Returns:
        dict: Standardized result dict:
            {
                "success": bool,
                "order_id": str or None,
                "status": str,
                "message": str,
                "response": dict,
                "order_report": dict or None,
            }
    """
    # -----------------------------------------------------------------
    # Step 1: Normalize & Validate All Arguments
    # -----------------------------------------------------------------
    clean_symbol = normalize_symbol(trading_symbol)
    clean_exchange = normalize_exchange_segment(exchange_segment)
    clean_action = normalize_transaction_type(transaction_type)
    clean_order_type = normalize_order_type(order_type)
    clean_product = normalize_product(product)
    clean_qty = normalize_quantity(quantity)
    clean_price = normalize_price(price, clean_order_type)
    clean_trigger_price = normalize_trigger_price(trigger_price, clean_order_type)
    clean_validity = normalize_validity(validity, clean_exchange)
    clean_amo = normalize_amo(amo)
    clean_dq = normalize_disclosed_quantity(disclosed_quantity, clean_qty)
    clean_tag = str(tag).strip() if tag else None

    # -----------------------------------------------------------------
    # Step 2: Ensure Authenticated NeoAPI Client
    # -----------------------------------------------------------------
    if client is None:
        if verbose:
            print(f"{Colors.DIM}[*] Obtaining authenticated Kotak Neo session...{Colors.RESET}")
        client = get_client()

    # -----------------------------------------------------------------
    # Step 3: Dispatch Order to Kotak Neo API
    # -----------------------------------------------------------------
    if verbose:
        print(
            f"[*] Sending {clean_action} order for {clean_qty} {clean_symbol} "
            f"({clean_exchange}, {clean_product}, {clean_order_type}) to Kotak Neo..."
        )

    try:
        raw_response = client.place_order(
            exchange_segment=clean_exchange,
            product=clean_product,
            price=clean_price,
            order_type=clean_order_type,
            quantity=clean_qty,
            validity=clean_validity,
            trading_symbol=clean_symbol,
            transaction_type=clean_action,
            amo=clean_amo,
            disclosed_quantity=clean_dq,
            trigger_price=clean_trigger_price,
            tag=clean_tag,
        )
    except Exception as exc:
        err_msg = f"Exception during place_order call: {exc}"
        if verbose:
            print(f"\n{Colors.RED}[X] Order Placement Failed: {err_msg}{Colors.RESET}\n")
        return {
            "success": False,
            "order_id": None,
            "status": "FAILED",
            "message": err_msg,
            "response": {"Error": str(exc)},
            "order_report": None,
        }

    # -----------------------------------------------------------------
    # Step 4: Parse Broker Response
    # -----------------------------------------------------------------
    order_id = extract_order_id(raw_response)
    error_msg = extract_order_error(raw_response)

    if not order_id and error_msg:
        if verbose:
            print(f"\n{Colors.RED}[X] Order Rejected / Failed by Kotak Neo:{Colors.RESET}")
            print(f"    {Colors.RED}{error_msg}{Colors.RESET}\n")
        return {
            "success": False,
            "order_id": None,
            "status": "REJECTED",
            "message": error_msg,
            "response": raw_response,
            "order_report": None,
        }

    if not order_id:
        # Fallback if no order ID and no explicit error
        err_msg = f"Unexpected response from broker: {raw_response}"
        if verbose:
            print(f"\n{Colors.YELLOW}[!] {err_msg}{Colors.RESET}\n")
        return {
            "success": False,
            "order_id": None,
            "status": "UNKNOWN",
            "message": err_msg,
            "response": raw_response,
            "order_report": None,
        }

    # -----------------------------------------------------------------
    # Step 5: Post-Placement Status Verification (via order_report)
    # -----------------------------------------------------------------
    live_status = "SUBMITTED"
    status_detail = "Order submitted to broker"
    report_data = None

    if check_status:
        try:
            order_report_res = client.order_report(order_id=order_id)
            if isinstance(order_report_res, dict):
                report_data = order_report_res
                # Parse order status from data list
                entries = order_report_res.get("data")
                if isinstance(entries, list) and len(entries) > 0:
                    entry = entries[0]
                    # Common status keys in Kotak Neo: ordSt, status
                    live_status = (entry.get("ordSt") or entry.get("status") or live_status).upper()
                    # Check rejection reason if rejected
                    rejection_reason = (
                        entry.get("rejRsn")
                        or entry.get("ordRejRsn")
                        or entry.get("cancelReason")
                    )
                    if rejection_reason:
                        status_detail = f"Rejected: {rejection_reason}"
                    else:
                        status_detail = f"Status: {live_status}"
        except Exception:
            # Order was already placed; order_report is non-critical enrichment
            pass

    # -----------------------------------------------------------------
    # Step 6: Display Success Confirmation Card
    # -----------------------------------------------------------------
    if verbose:
        status_color = Colors.GREEN
        if live_status in ["REJECTED", "CANCELLED"]:
            status_color = Colors.RED
        elif live_status in ["OPEN", "PENDING", "SUBMITTED"]:
            status_color = Colors.CYAN

        print_banner("ORDER PLACEMENT RESULT", status_color)
        print(f"  {Colors.BOLD}Broker Order ID :{Colors.RESET} {Colors.GREEN}{Colors.BOLD}{order_id}{Colors.RESET}")
        print(f"  {Colors.BOLD}Trading Symbol  :{Colors.RESET} {clean_symbol}")
        print(f"  {Colors.BOLD}Exchange Segment:{Colors.RESET} {clean_exchange}")
        print(f"  {Colors.BOLD}Action          :{Colors.RESET} {clean_action} ({'BUY' if clean_action == 'B' else 'SELL'})")
        print(f"  {Colors.BOLD}Order Type      :{Colors.RESET} {clean_order_type}")
        print(f"  {Colors.BOLD}Quantity        :{Colors.RESET} {clean_qty}")
        print(f"  {Colors.BOLD}Price           :{Colors.RESET} Rs. {clean_price if clean_order_type in ['L', 'SL'] else 'MARKET'}")
        print(f"  {Colors.BOLD}Order Status    :{Colors.RESET} {status_color}{Colors.BOLD}{live_status}{Colors.RESET}")
        if status_detail and live_status in ["REJECTED", "CANCELLED"]:
            print(f"  {Colors.BOLD}Reason          :{Colors.RESET} {Colors.RED}{status_detail}{Colors.RESET}")
        print("=" * 62)

    return {
        "success": True,
        "order_id": order_id,
        "status": live_status,
        "message": status_detail,
        "response": raw_response,
        "order_report": report_data,
    }


# =====================================================================
# Interactive Prompt Mode
# =====================================================================

def interactive_prompt() -> Dict[str, Any]:
    """Guides the user step-by-step through entering order parameters."""
    print_banner("KOTAK NEO - INTERACTIVE ORDER PLACEMENT", Colors.CYAN)
    print(f"{Colors.DIM}Enter the details below. Press Enter to accept bracketed default values.{Colors.RESET}\n")

    # 1. Trading Symbol
    while True:
        symbol = input(f"{Colors.BOLD}1. Trading Symbol (e.g. INFY-EQ, RELIANCE-EQ, TCS-EQ): {Colors.RESET}").strip()
        if symbol:
            try:
                symbol = normalize_symbol(symbol)
                break
            except ValueError as e:
                print(f"   {Colors.RED}[!] {e}{Colors.RESET}")
        else:
            print(f"   {Colors.RED}[!] Trading symbol cannot be empty.{Colors.RESET}")

    # 2. Exchange Segment
    print(f"\n{Colors.BOLD}2. Exchange Segment:{Colors.RESET}")
    print("   1) nse_cm  - NSE Cash / Equities (Default)")
    print("   2) bse_cm  - BSE Cash / Equities")
    print("   3) nse_fo  - NSE Futures & Options")
    print("   4) bse_fo  - BSE Futures & Options")
    print("   5) mcx_fo  - MCX Commodities")
    print("   6) nse_com - NSE Commodities")
    seg_choice = input(f"   Select option [1-6] or code (default: nse_cm): ").strip()
    seg_map = {
        "1": "nse_cm",
        "2": "bse_cm",
        "3": "nse_fo",
        "4": "bse_fo",
        "5": "mcx_fo",
        "6": "nse_com",
    }
    exchange = seg_map.get(seg_choice, seg_choice if seg_choice else "nse_cm")
    exchange = normalize_exchange_segment(exchange)

    # 3. Transaction Type (BUY / SELL)
    print(f"\n{Colors.BOLD}3. Transaction Type:{Colors.RESET}")
    print("   1) BUY  (B)")
    print("   2) SELL (S)")
    action_choice = input("   Select [1/2 or BUY/SELL] (default: BUY): ").strip()
    if action_choice in ["1", ""]:
        action = "B"
    elif action_choice == "2":
        action = "S"
    else:
        action = normalize_transaction_type(action_choice)

    # 4. Product Type
    print(f"\n{Colors.BOLD}4. Product Type:{Colors.RESET}")
    print("   1) CNC  - Cash & Carry (Delivery / Investment) [Default]")
    print("   2) MIS  - Margin Intraday Square-off (Day Trading)")
    print("   3) NRML - Normal (F&O Carryforward)")
    print("   4) MTF  - Margin Trade Funding")
    prod_choice = input("   Select [1-4] or code (default: CNC): ").strip()
    prod_map = {"1": "CNC", "2": "MIS", "3": "NRML", "4": "MTF"}
    product = prod_map.get(prod_choice, prod_choice if prod_choice else "CNC")
    product = normalize_product(product)

    # 5. Order Type
    print(f"\n{Colors.BOLD}5. Order Type:{Colors.RESET}")
    print("   1) L    - Limit Order [Default]")
    print("   2) MKT  - Market Order")
    print("   3) SL   - Stop Loss Limit")
    print("   4) SL-M - Stop Loss Market")
    type_choice = input("   Select [1-4] or code (default: L): ").strip()
    type_map = {"1": "L", "2": "MKT", "3": "SL", "4": "SL-M"}
    order_type = type_map.get(type_choice, type_choice if type_choice else "L")
    order_type = normalize_order_type(order_type)

    # 6. Quantity
    while True:
        qty_input = input(f"\n{Colors.BOLD}6. Quantity (shares/lots) [default: 1]: {Colors.RESET}").strip()
        qty_input = qty_input if qty_input else "1"
        try:
            quantity = normalize_quantity(qty_input)
            break
        except ValueError as e:
            print(f"   {Colors.RED}[!] {e}{Colors.RESET}")

    # 7. Price
    price = "0"
    if order_type in ["L", "SL"]:
        while True:
            price_input = input(f"\n{Colors.BOLD}7. Limit Price (Rs.): {Colors.RESET}").strip()
            try:
                price = normalize_price(price_input, order_type)
                break
            except ValueError as e:
                print(f"   {Colors.RED}[!] {e}{Colors.RESET}")
    else:
        print(f"\n{Colors.DIM}7. Price: Set automatically to 0 for MARKET order.{Colors.RESET}")

    # 8. Trigger Price
    trigger_price = "0"
    if order_type in ["SL", "SL-M"]:
        while True:
            tp_input = input(f"\n{Colors.BOLD}8. Trigger Price (Rs.): {Colors.RESET}").strip()
            try:
                trigger_price = normalize_trigger_price(tp_input, order_type)
                break
            except ValueError as e:
                print(f"   {Colors.RED}[!] {e}{Colors.RESET}")

    # 9. Validity
    val_input = input(f"\n{Colors.BOLD}9. Validity (DAY / IOC) [default: DAY]: {Colors.RESET}").strip()
    validity = normalize_validity(val_input if val_input else "DAY", exchange)

    # 10. AMO (After Market Order)
    amo_input = input(f"\n{Colors.BOLD}10. Place as AMO (After Market Order)? (y/n) [default: n]: {Colors.RESET}").strip()
    amo = "YES" if amo_input.lower() in ["y", "yes"] else "NO"

    return {
        "trading_symbol": symbol,
        "exchange_segment": exchange,
        "transaction_type": action,
        "product": product,
        "order_type": order_type,
        "quantity": quantity,
        "price": price,
        "trigger_price": trigger_price,
        "validity": validity,
        "amo": amo,
    }


# =====================================================================
# CLI Argument Parser & Main
# =====================================================================

def build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser for order placement."""
    parser = argparse.ArgumentParser(
        prog="orderplacement.py",
        description="Place orders directly with Kotak Neo broker using Kotak Neo API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  1. Buy 1 share of Reliance at Limit price Rs. 2950.50 (NSE Cash, Delivery):
     python orderplacement.py -s RELIANCE-EQ -a BUY -q 1 -p 2950.50 -t LIMIT -e nse_cm --product CNC

  2. Buy 10 shares of Infosys at Market price:
     python orderplacement.py -s INFY-EQ -a BUY -q 10 -t MKT -y

  3. Sell 5 shares Intraday at Market price:
     python orderplacement.py -s TATAMOTORS-EQ -a SELL -q 5 -t MKT --product MIS -y

  4. Buy 1 Lot Nifty Call option with Stop Loss:
     python orderplacement.py -s NIFTY24DEC24000CE -e nse_fo -a BUY -q 25 -t SL -p 120.00 --tp 110.00 --product NRML

  5. Interactive Wizard:
     python orderplacement.py
        """,
    )

    # Core Parameters
    parser.add_argument(
        "-s", "--symbol",
        type=str,
        default=None,
        help="Trading symbol (e.g. RELIANCE-EQ, INFY-EQ, NIFTY24DEC24000CE).",
    )
    parser.add_argument(
        "-a", "--action",
        type=str,
        default=None,
        choices=["BUY", "buy", "b", "B", "SELL", "sell", "s", "S"],
        help="Transaction type: BUY (or B) / SELL (or S).",
    )
    parser.add_argument(
        "-q", "--qty", "--quantity",
        type=str,
        default="1",
        help="Order quantity in shares or lots (default: 1).",
    )
    parser.add_argument(
        "-p", "--price",
        type=str,
        default=None,
        help="Order price (mandatory for LIMIT and SL orders; 0 for MKT).",
    )
    parser.add_argument(
        "-t", "--type", "--order-type",
        dest="order_type",
        type=str,
        default="L",
        help="Order type: L (Limit), MKT (Market), SL (Stop Loss Limit), SL-M (Stop Loss Market). Default: L.",
    )
    parser.add_argument(
        "-e", "--exchange", "--segment",
        dest="exchange",
        type=str,
        default="nse_cm",
        help="Exchange segment: nse_cm, bse_cm, nse_fo, bse_fo, mcx_fo, nse_com (default: nse_cm).",
    )
    parser.add_argument(
        "--product",
        type=str,
        default="CNC",
        help="Product code: CNC (Delivery), MIS (Intraday), NRML (Normal F&O), MTF (default: CNC).",
    )
    parser.add_argument(
        "--validity",
        type=str,
        default="DAY",
        choices=["DAY", "day", "IOC", "ioc"],
        help="Order validity: DAY or IOC (default: DAY).",
    )
    parser.add_argument(
        "--tp", "--trigger-price",
        dest="trigger_price",
        type=str,
        default="0",
        help="Trigger price for Stop Loss orders (SL / SL-M). Default: 0.",
    )
    parser.add_argument(
        "--amo",
        type=str,
        default="NO",
        choices=["YES", "yes", "NO", "no", "Y", "N"],
        help="After Market Order (YES or NO, default: NO).",
    )
    parser.add_argument(
        "--dq", "--disclosed-qty",
        dest="disclosed_qty",
        type=str,
        default="0",
        help="Disclosed quantity (default: 0).",
    )
    parser.add_argument(
        "--tag",
        type=str,
        default=None,
        help="Optional order tag/marker for strategy tracking.",
    )

    # Behavior Flags
    parser.add_argument(
        "-y", "--yes", "--no-confirm",
        dest="no_confirm",
        action="store_true",
        help="Execute immediately without interactive confirmation prompt.",
    )
    parser.add_argument(
        "-i", "--interactive",
        action="store_true",
        help="Launch the interactive order placement wizard.",
    )

    return parser


def main() -> None:
    """CLI entry point for order placement."""
    parser = build_argument_parser()
    args = parser.parse_args()

    # Determine if we should enter interactive mode
    no_args_provided = (args.symbol is None and args.action is None and not args.interactive)
    run_interactive = args.interactive or no_args_provided

    try:
        if run_interactive:
            params = interactive_prompt()
            no_confirm = args.no_confirm
        else:
            # Validate required CLI parameters
            if not args.symbol:
                print(f"{Colors.RED}[!] Missing required argument: --symbol / -s{Colors.RESET}")
                parser.print_help()
                sys.exit(1)
            if not args.action:
                print(f"{Colors.RED}[!] Missing required argument: --action / -a (BUY or SELL){Colors.RESET}")
                parser.print_help()
                sys.exit(1)

            clean_order_type = normalize_order_type(args.order_type)
            if clean_order_type in ["L", "SL"] and args.price is None:
                print(
                    f"{Colors.RED}[!] Price (--price / -p) is required for {clean_order_type} (Limit) order.{Colors.RESET}"
                )
                sys.exit(1)

            params = {
                "trading_symbol": args.symbol,
                "exchange_segment": args.exchange,
                "transaction_type": args.action,
                "product": args.product,
                "order_type": args.order_type,
                "quantity": args.qty,
                "price": args.price or "0",
                "trigger_price": args.trigger_price,
                "validity": args.validity,
                "amo": args.amo,
                "disclosed_quantity": args.disclosed_qty,
                "tag": args.tag,
            }
            no_confirm = args.no_confirm

        # Normalize parameters for display
        norm_symbol = normalize_symbol(params["trading_symbol"])
        norm_exchange = normalize_exchange_segment(params["exchange_segment"])
        norm_action = normalize_transaction_type(params["transaction_type"])
        norm_type = normalize_order_type(params["order_type"])
        norm_product = normalize_product(params["product"])
        norm_qty = normalize_quantity(params["quantity"])
        norm_price = normalize_price(params.get("price"), norm_type)
        norm_tp = normalize_trigger_price(params.get("trigger_price"), norm_type)
        norm_validity = normalize_validity(params.get("validity", "DAY"), norm_exchange)
        norm_amo = normalize_amo(params.get("amo", "NO"))
        norm_dq = normalize_disclosed_quantity(params.get("disclosed_quantity", "0"), norm_qty)
        tag = params.get("tag")

        # Display Summary Card
        display_order_summary_card(
            symbol=norm_symbol,
            exchange=norm_exchange,
            action=norm_action,
            product=norm_product,
            order_type=norm_type,
            quantity=norm_qty,
            price=norm_price,
            trigger_price=norm_tp,
            validity=norm_validity,
            amo=norm_amo,
            disclosed_quantity=norm_dq,
            tag=tag,
        )

        # Confirm before placing unless -y / --no-confirm was passed
        if not no_confirm:
            action_name = "BUY" if norm_action == "B" else "SELL"
            confirm = input(
                f"{Colors.YELLOW}Are you sure you want to place this {action_name} order in Kotak Neo? (y/n) [y]: {Colors.RESET}"
            ).strip().lower()
            if confirm not in ["", "y", "yes"]:
                print(f"\n{Colors.YELLOW}[!] Order placement cancelled by user.{Colors.RESET}\n")
                sys.exit(0)

        # Execute Order Placement
        result = place_order(
            trading_symbol=norm_symbol,
            exchange_segment=norm_exchange,
            transaction_type=norm_action,
            product=norm_product,
            order_type=norm_type,
            quantity=norm_qty,
            price=norm_price,
            trigger_price=norm_tp,
            validity=norm_validity,
            amo=norm_amo,
            disclosed_quantity=norm_dq,
            tag=tag,
            check_status=True,
            verbose=True,
        )

        if not result["success"]:
            sys.exit(1)

    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}[!] Operation cancelled by user.{Colors.RESET}")
        sys.exit(0)
    except Exception as exc:
        print(f"\n{Colors.RED}[X] Error: {exc}{Colors.RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
