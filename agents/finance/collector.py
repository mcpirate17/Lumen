"""Finance data collector. Ported from OpenClaw collect.sh.

Sources (all free, no API keys):
  - CoinGecko: Top 50 crypto by market cap
  - Alternative.me: Fear & Greed Index (7-day history)
  - Yahoo Finance: Stock screeners (gainers, losers, most active, trending)
  - DuckDuckGo: Fallback when Yahoo rate-limits
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger("lumen.finance.collector")

_UA = "LumenFinanceBot/1.0"
_TIMEOUT = 20.0

_YF_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://finance.yahoo.com/",
}


@dataclass(slots=True)
class CryptoData:
    symbol: str
    name: str
    price: float
    change_1h: float
    change_24h: float
    change_7d: float
    market_cap: float
    volume_24h: float


@dataclass(slots=True)
class FearGreed:
    value: int
    classification: str
    date: str
    history: list[dict] = field(default_factory=list)
    trend: str = "STABLE"
    avg_7d: float = 0.0


@dataclass(slots=True)
class StockQuote:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: int


@dataclass(slots=True)
class MarketSnapshot:
    timestamp: str
    crypto: list[CryptoData] = field(default_factory=list)
    fear_greed: FearGreed | None = None
    gainers: list[StockQuote] = field(default_factory=list)
    losers: list[StockQuote] = field(default_factory=list)
    most_active: list[StockQuote] = field(default_factory=list)
    trending: list[StockQuote] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    source_stock: str = "yahoo"


def _fmt_mcap(mcap: float) -> str:
    if mcap >= 1e9:
        return f"${mcap / 1e9:.1f}B"
    if mcap >= 1e6:
        return f"${mcap / 1e6:.0f}M"
    return f"${mcap:,.0f}"


def _fmt_price(price: float) -> str:
    if price >= 1000:
        return f"${price:,.0f}"
    if price >= 1:
        return f"${price:,.2f}"
    return f"${price:.6f}"


def _fmt_vol(vol: int) -> str:
    if vol >= 1e9:
        return f"{vol / 1e9:.1f}B"
    if vol >= 1e6:
        return f"{vol / 1e6:.1f}M"
    return f"{vol / 1e3:.0f}K"


async def fetch_crypto(count: int = 50) -> list[CryptoData]:
    """Fetch top crypto by market cap from CoinGecko."""
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        f"?vs_currency=usd&order=market_cap_desc&per_page={count}&page=1"
        "&sparkline=false&price_change_percentage=1h,24h,7d"
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": _UA, "Accept": "application/json"})
            resp.raise_for_status()
            coins = resp.json()
        except Exception as e:
            log.warning("CoinGecko failed: %s", e)
            return []

    results = []
    for c in coins:
        results.append(CryptoData(
            symbol=c.get("symbol", "?").upper(),
            name=c.get("name", "?"),
            price=c.get("current_price", 0) or 0,
            change_1h=c.get("price_change_percentage_1h_in_currency") or 0,
            change_24h=c.get("price_change_percentage_24h") or 0,
            change_7d=c.get("price_change_percentage_7d_in_currency") or 0,
            market_cap=c.get("market_cap", 0) or 0,
            volume_24h=c.get("total_volume", 0) or 0,
        ))
    return results


async def fetch_fear_greed() -> FearGreed | None:
    """Fetch 7-day Fear & Greed Index from alternative.me."""
    url = "https://api.alternative.me/fng/?limit=7&format=json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": _UA})
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("Fear & Greed API failed: %s", e)
            return None

    entries = data.get("data", [])
    if not entries:
        return None

    latest = entries[0]
    value = int(latest.get("value", 0))
    classification = latest.get("value_classification", "Unknown")
    ts = int(latest.get("timestamp", 0))
    dt = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "N/A"

    history = []
    vals = []
    for e in entries[:7]:
        v = int(e.get("value", 0))
        cl = e.get("value_classification", "?")
        ts2 = int(e.get("timestamp", 0))
        dt2 = datetime.fromtimestamp(ts2).strftime("%Y-%m-%d") if ts2 else "N/A"
        vals.append(v)
        history.append({"date": dt2, "value": v, "classification": cl})

    trend = "STABLE"
    avg = 0.0
    if len(vals) >= 2:
        diff = vals[0] - vals[-1]
        trend = "IMPROVING" if diff > 5 else "DETERIORATING" if diff < -5 else "STABLE"
        avg = sum(vals) / len(vals)

    return FearGreed(
        value=value, classification=classification, date=dt,
        history=history, trend=trend, avg_7d=avg,
    )


async def _fetch_yf_screener(scr_id: str, count: int = 10) -> list[StockQuote] | None:
    """Fetch a Yahoo Finance predefined screener."""
    url = (
        f"https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"
        f"?formatted=false&lang=en-US&region=US&start=0&count={count}&scrIds={scr_id}"
    )
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(url, headers=_YF_HEADERS)
            if resp.status_code == 429:
                return None  # rate limited
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError:
            return None
        except Exception as e:
            log.warning("Yahoo Finance %s failed: %s", scr_id, e)
            return None

    quotes = (data.get("finance", {}).get("result", [{}])[0].get("quotes", []))
    return [
        StockQuote(
            symbol=q.get("symbol", "?"),
            name=(q.get("longName") or q.get("shortName") or "?")[:40],
            price=q.get("regularMarketPrice", 0) or 0,
            change_pct=q.get("regularMarketChangePercent", 0) or 0,
            volume=q.get("regularMarketVolume", 0) or 0,
        )
        for q in quotes[:count]
    ]


async def _fetch_yf_trending(count: int = 15) -> list[StockQuote]:
    """Fetch trending tickers from Yahoo Finance."""
    url = f"https://query2.finance.yahoo.com/v1/finance/trending/US?count={count}&useQuotes=true"
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(url, headers=_YF_HEADERS)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("Yahoo trending failed: %s", e)
            return []

    quotes = data.get("finance", {}).get("result", [{}])[0].get("quotes", [])
    return [
        StockQuote(
            symbol=q.get("symbol", "?"),
            name=(q.get("longName") or q.get("shortName") or "?")[:40],
            price=q.get("regularMarketPrice", 0) or 0,
            change_pct=q.get("regularMarketChangePercent", 0) or 0,
            volume=q.get("regularMarketVolume", 0) or 0,
        )
        for q in quotes[:count]
    ]


def _detect_signals(crypto: list[CryptoData], stocks: list[StockQuote]) -> dict:
    """Detect notable market signals."""
    signals = {"oversold_crypto": [], "momentum_crypto": [], "high_volume_crypto": [],
               "big_gainers": [], "big_losers": [], "high_volume_stocks": []}

    for c in crypto:
        if c.change_7d < -10:
            signals["oversold_crypto"].append(c)
        if c.change_7d > 10:
            signals["momentum_crypto"].append(c)
        if c.market_cap > 0 and c.volume_24h / c.market_cap > 0.15:
            signals["high_volume_crypto"].append(c)

    for s in stocks:
        if s.change_pct > 10:
            signals["big_gainers"].append(s)
        elif s.change_pct < -10:
            signals["big_losers"].append(s)
        if s.volume > 50_000_000:
            signals["high_volume_stocks"].append(s)

    return signals


async def collect_all() -> MarketSnapshot:
    """Collect all market data. Main entry point."""
    import asyncio

    timestamp = datetime.now(timezone.utc).isoformat()

    # Fetch everything concurrently
    crypto_task = asyncio.create_task(fetch_crypto())
    fg_task = asyncio.create_task(fetch_fear_greed())
    gainers_task = asyncio.create_task(_fetch_yf_screener("day_gainers"))
    losers_task = asyncio.create_task(_fetch_yf_screener("day_losers"))
    actives_task = asyncio.create_task(_fetch_yf_screener("most_actives"))
    trending_task = asyncio.create_task(_fetch_yf_trending())

    crypto = await crypto_task
    fear_greed = await fg_task
    gainers = await gainers_task
    losers = await losers_task
    most_active = await actives_task
    trending = await trending_task

    source = "yahoo"
    if gainers is None and losers is None and most_active is None:
        source = "unavailable"

    all_stocks = []
    for q_list in [gainers, losers, most_active]:
        if q_list:
            all_stocks.extend(q_list)

    signals = _detect_signals(crypto, all_stocks)

    return MarketSnapshot(
        timestamp=timestamp,
        crypto=crypto,
        fear_greed=fear_greed,
        gainers=gainers or [],
        losers=losers or [],
        most_active=most_active or [],
        trending=trending,
        signals=signals,
        source_stock=source,
    )


def snapshot_to_text(snap: MarketSnapshot) -> str:
    """Convert a MarketSnapshot to LLM-readable plain text."""
    lines = [f"=== MARKET DATA (collected {snap.timestamp}) ===\n"]

    # Crypto
    if snap.crypto:
        lines.append("--- TOP CRYPTO (CoinGecko) ---")
        lines.append(f"{'#':<4} {'Sym':<8} {'Name':<18} {'Price':>13} {'1h%':>7} {'24h%':>8} {'7d%':>8} {'MCap':>14}")
        lines.append("-" * 88)
        for i, c in enumerate(snap.crypto[:20], 1):
            lines.append(
                f"{i:<4} {c.symbol:<8} {c.name[:17]:<18} {_fmt_price(c.price):>13} "
                f"{c.change_1h:>+6.2f}% {c.change_24h:>+7.2f}% {c.change_7d:>+7.2f}% "
                f"{_fmt_mcap(c.market_cap):>14}"
            )
        lines.append("")

    # Fear & Greed
    if snap.fear_greed:
        fg = snap.fear_greed
        lines.append("--- FEAR & GREED INDEX ---")
        lines.append(f"Current: {fg.value}/100 — {fg.classification}")
        lines.append(f"7-day trend: {fg.trend} (avg {fg.avg_7d:.1f})")
        for h in fg.history:
            lines.append(f"  {h['date']}: {h['value']}/100 — {h['classification']}")
        lines.append("")

    # Stocks
    for label, data in [("TOP GAINERS", snap.gainers), ("TOP LOSERS", snap.losers),
                         ("MOST ACTIVE", snap.most_active)]:
        if data:
            lines.append(f"--- {label} (Yahoo Finance) ---")
            for q in data[:10]:
                lines.append(
                    f"  {q.symbol:<8} {q.name:<28} ${q.price:>8.2f} "
                    f"{q.change_pct:>+7.2f}% {_fmt_vol(q.volume):>10}"
                )
            lines.append("")

    if snap.trending:
        lines.append("--- TRENDING TICKERS ---")
        for q in snap.trending[:10]:
            lines.append(f"  {q.symbol:<8} {q.name:<30} ${q.price:>9.2f} {q.change_pct:>+7.2f}%")
        lines.append("")

    # Signals
    sigs = snap.signals
    if any(sigs.values()):
        lines.append("--- SIGNALS ---")
        if sigs.get("oversold_crypto"):
            lines.append("Oversold crypto (7d < -10%):")
            for c in sigs["oversold_crypto"]:
                lines.append(f"  {c.name} ({c.symbol}): {_fmt_price(c.price)} | 7d: {c.change_7d:+.2f}%")
        if sigs.get("momentum_crypto"):
            lines.append("Momentum crypto (7d > +10%):")
            for c in sigs["momentum_crypto"]:
                lines.append(f"  {c.name} ({c.symbol}): {_fmt_price(c.price)} | 7d: {c.change_7d:+.2f}%")
        if sigs.get("big_gainers"):
            lines.append("Big stock movers (>10%):")
            for s in sigs["big_gainers"][:5]:
                lines.append(f"  {s.name} ({s.symbol}): ${s.price:.2f} {s.change_pct:+.2f}%")
        lines.append("")

    return "\n".join(lines)
