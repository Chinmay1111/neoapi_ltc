# Kotak Neo API - Login & Authentication

This project provides authentication and login utilities for the Kotak Neo Broker API using the official `kotak-neo-python` SDK (`neo_api_client`).

---

## 🚀 Quick Start

### 1. Run Interactive Login
You can run `login.py` directly. If credentials are not present in `.env`, the script prompts you interactively in the terminal:

```bash
python login.py
```

### 2. (Optional) Automated Login via `.env`
To avoid entering credentials every time, copy `.env.example` to `.env`:

```bash
copy .env.example .env
```

Open `.env` and fill in your details:
```env
# Consumer Key from Neo App/Web (More -> Trade API -> Generate Application)
NEO_CONSUMER_KEY=your-consumer-key-token-here

# Mobile number registered with Kotak Neo (e.g. +919876543210 or 9876543210)
NEO_MOBILE_NUMBER=+919876543210

# Unique Client Code (from Profile section)
NEO_UCC=ABC123

# Your 6-digit trading MPIN
NEO_MPIN=123456

# (Optional) TOTP Secret Key from TOTP Registration for auto-generating TOTP codes
NEO_TOTP_SECRET=your-totp-secret-key
```

Now, running `python login.py` will automatically authenticate and display:
```text
==============================================================
                       LOGIN SUCCESSFUL                       
==============================================================
  Client Name   : [YOUR NAME]
  Client UCC    : [YOUR UCC]
  PAN Card      : ABCDE****F
  Client Type   : RI
  Access Level  : Trade (Full Trading Access)
  Data Center   : E43
  Session ID    : 8f4b23...
  Account Status: Active
==============================================================
```

---

## 🔑 Where to Find Credentials

1. **Consumer Key (API Key)**:
   - Login to [Kotak Securities / Neo Web](https://www.kotaksecurities.com) or Mobile App.
   - Go to **More** (or Invest) -> **Trade API** card.
   - Click **Generate application** (or view default application) and copy the **Token**.
2. **UCC (User Client Code)**:
   - Available in the Kotak Neo app/web under the **Profile** section.
3. **TOTP Registration**:
   - Go to **More** -> **Trade API** -> **TOTP Registration** in the top-right menu.
   - Scan the QR code with Google Authenticator or Microsoft Authenticator.
   - *(Tip)*: Copy the text setup key provided under the QR code into `NEO_TOTP_SECRET` so `login.py` can auto-generate TOTPs!
4. **MPIN**:
   - Your 6-digit Mobile PIN set up for login and 2FA in Kotak Neo.

---

## 🐍 Using in Trading Scripts

You can import the authenticated client into any trading or order management script:

```python
from login import get_client

# Automatically logs in and returns authenticated NeoAPI instance
client = get_client()

# Place orders, fetch portfolio, or get live quotes
quotes = client.quotes(
    instrument_tokens=[{"instrument_token": "1333", "exchange_segment": "nse_cm"}],
    quote_type="all"
)
print(quotes)
```

---

## 📈 Placing Orders (`orderplacement.py`)

`orderplacement.py` allows you to place any order (Equity Cash, F&O, Commodity, Delivery, Intraday, Limit, Market, Stop-Loss, etc.) with your Kotak Neo broker.

### 1. Interactive Wizard (Guided Prompt)
Simply run the script with no arguments:
```bash
python orderplacement.py
```
It guides you through selecting symbol, exchange, order type, product, quantity, price, and displays an order confirmation summary before placing.

### 2. Command Line Interface (CLI)
You can supply all arguments directly via flags:

```bash
# 1. Buy 1 share of Reliance at Limit price Rs. 2950.50 (NSE Cash, Delivery CNC):
python orderplacement.py --symbol RELIANCE-EQ --action BUY --qty 1 --price 2950.50 --type LIMIT --exchange nse_cm --product CNC

# 2. Buy 10 shares of Infosys at Market price with immediate execution (-y skips confirmation):
python orderplacement.py -s INFY-EQ -a BUY -q 10 -t MKT -y

# 3. Sell 5 shares Intraday (MIS) at Market price:
python orderplacement.py -s TATAMOTORS-EQ -a SELL -q 5 -t MKT --product MIS -y

# 4. Buy 1 Lot Nifty Call option with Stop Loss:
python orderplacement.py -s NIFTY24DEC24000CE -e nse_fo -a BUY -q 25 -t SL -p 120.00 --tp 110.00 --product NRML -y

# 5. Place After Market Order (AMO):
python orderplacement.py -s TCS-EQ -a BUY -q 2 -p 3500.00 -t L --amo YES -y
```

### 3. Programmatic Python API
Use directly in your algo bots or automated scripts:

```python
from orderplacement import place_order

# Places order and automatically verifies broker order report/status
result = place_order(
    trading_symbol="RELIANCE-EQ",
    transaction_type="BUY",       # "BUY" / "B" or "SELL" / "S"
    quantity=1,
    order_type="LIMIT",           # "LIMIT" / "L", "MARKET" / "MKT", "SL", "SL-M"
    price=2950.50,
    exchange_segment="nse_cm",    # "nse_cm", "bse_cm", "nse_fo", "bse_fo", "mcx_fo"
    product="CNC",                # "CNC", "MIS", "NRML", "MTF"
    validity="DAY",               # "DAY" or "IOC"
)

if result["success"]:
    print(f"Order Placed! ID: {result['order_id']}, Status: {result['status']}")
else:
    print(f"Order Failed: {result['message']}")
```

---

## 🧪 Testing

Run the automated unit tests:

```bash
python -m unittest discover -s . -p "test_*.py"
```

