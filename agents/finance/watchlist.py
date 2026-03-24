"""Personal watchlist manager for Lumen finance agent.

User says "watch NVDA" → tracked. "Drop NVDA" → removed.
Stores in SQLite watchlist table.
"""

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("lumen.finance.watchlist")

_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://finance.yahoo.com/",
}


@dataclass(slots=True)
class WatchlistQuote:
    symbol: str
    name: str
    price: float
    change_pct: float
    asset_type: str  # stock, crypto, bond, etf


async def add_to_watchlist(db_func, symbol: str, asset_type: str = "stock", name: str = "") -> bool:
    """Add a symbol to the watchlist."""
    from server.database import get_db
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO watchlist (symbol, asset_type, name)
               VALUES (?, ?, ?)""",
            (symbol.upper(), asset_type, name),
        )
        await db.commit()
        return True
    finally:
        await db.close()


async def remove_from_watchlist(symbol: str) -> bool:
    """Remove a symbol from the watchlist."""
    from server.database import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM watchlist WHERE symbol = ?", (symbol.upper(),)
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def get_watchlist() -> list[dict]:
    """Get all watchlist entries."""
    from server.database import get_db
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM watchlist ORDER BY asset_type, symbol") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        await db.close()


async def fetch_watchlist_quotes() -> list[WatchlistQuote]:
    """Fetch current prices for all watchlist items."""
    items = await get_watchlist()
    if not items:
        return []

    quotes = []
    stock_symbols = [i["symbol"] for i in items if i["asset_type"] in ("stock", "etf")]
    crypto_symbols = [i["symbol"] for i in items if i["asset_type"] == "crypto"]

    # Fetch stock quotes from Yahoo Finance
    if stock_symbols:
        symbols_str = ",".join(stock_symbols)
        url = f"https://query2.finance.yahoo.com/v7/finance/quote?symbols={symbols_str}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(url, headers=_YF_HEADERS)
                resp.raise_for_status()
                data = resp.json()
                for q in data.get("quoteResponse", {}).get("result", []):
                    quotes.append(WatchlistQuote(
                        symbol=q.get("symbol", "?"),
                        name=(q.get("longName") or q.get("shortName") or "?")[:40],
                        price=q.get("regularMarketPrice", 0) or 0,
                        change_pct=q.get("regularMarketChangePercent", 0) or 0,
                        asset_type="stock",
                    ))
            except Exception as e:
                log.warning("Yahoo quote fetch failed: %s", e)

    # Fetch crypto quotes from CoinGecko
    if crypto_symbols:
        ids_str = ",".join(s.lower() for s in crypto_symbols)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_str}&vs_currencies=usd&include_24hr_change=true"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(url, headers={"User-Agent": "LumenFinanceBot/1.0"})
                resp.raise_for_status()
                data = resp.json()
                for coin_id, vals in data.items():
                    quotes.append(WatchlistQuote(
                        symbol=coin_id.upper(),
                        name=coin_id.title(),
                        price=vals.get("usd", 0),
                        change_pct=vals.get("usd_24h_change", 0) or 0,
                        asset_type="crypto",
                    ))
            except Exception as e:
                log.warning("CoinGecko watchlist fetch failed: %s", e)

    return quotes


def watchlist_to_text(quotes: list[WatchlistQuote]) -> str:
    """Format watchlist quotes as plain text."""
    if not quotes:
        return "Your watchlist is empty. Say 'watch NVDA' to add a symbol."

    lines = ["=== YOUR WATCHLIST ==="]
    for q in quotes:
        sign = "+" if q.change_pct >= 0 else ""
        lines.append(f"  {q.symbol:<8} {q.name:<30} ${q.price:>9.2f} {sign}{q.change_pct:.2f}%")
    return "\n".join(lines)
