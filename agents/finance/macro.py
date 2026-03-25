"""Macro economic data for Lumen finance.

Fetches:
  - Treasury yields (2Y, 10Y, 30Y) and yield curve spread from FRED
  - Fed funds rate from FRED
  - US Dollar Index from FRED
  - CPI / inflation from FRED
  - VIX (fear index) from Yahoo Finance
  - Put/Call ratio from Yahoo Finance options chains

FRED requires a free API key: https://fred.stlouisfed.org/docs/api/api_key.html
Set FRED_API_KEY in environment or lumen.yaml.

If FRED key is not available, falls back to yfinance for treasury yields (^TNX, ^FVX, ^TYX).
"""

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

log = logging.getLogger("lumen.finance.macro")


@dataclass(slots=True)
class MacroSnapshot:
    timestamp: str = ""
    # Treasury yields
    yield_2y: float | None = None
    yield_10y: float | None = None
    yield_30y: float | None = None
    yield_curve_spread: float | None = None  # 10Y - 2Y
    yield_curve_signal: str = ""  # "normal" / "flat" / "inverted"
    # Fed funds
    fed_funds_rate: float | None = None
    # Dollar
    dollar_index: float | None = None
    dollar_trend: str = ""  # "strengthening" / "weakening" / "stable"
    # Inflation
    cpi_yoy: float | None = None  # year-over-year CPI change %
    # VIX
    vix: float | None = None
    vix_signal: str = ""  # "low_fear" / "elevated" / "high_fear" / "extreme_fear"
    vix_change_1w: float | None = None
    # Put/Call
    put_call_ratio: float | None = None  # market-wide or SPY
    put_call_signal: str = ""  # "bearish" / "neutral" / "bullish" (contrarian)
    # Signals
    signals: list[str] = field(default_factory=list)


def _get_fred_key() -> str | None:
    """Get FRED API key from env or config."""
    key = os.environ.get("FRED_API_KEY")
    if key:
        return key
    # Try config file
    try:
        from server.config import load_config
        cfg = load_config()
        return cfg.finance.fred_api_key or None
    except Exception:
        return None


async def fetch_fred_data() -> dict:
    """Fetch macro data from FRED API. Returns raw series data."""
    key = _get_fred_key()
    if not key:
        log.info("No FRED_API_KEY set — using yfinance fallback for yields")
        return {}

    try:
        from fredapi import Fred
        fred = Fred(api_key=key)
    except ImportError:
        log.warning("fredapi not installed. Run: pip install fredapi")
        return {}

    data = {}
    series_map = {
        "yield_2y": "DGS2",
        "yield_10y": "DGS10",
        "yield_30y": "DGS30",
        "fed_funds": "DFF",
        "dollar_index": "DTWEXBGS",
        "cpi": "CPIAUCSL",
        "breakeven_10y": "T10YIE",
    }

    for name, series_id in series_map.items():
        try:
            s = fred.get_series(series_id, observation_start=(
                datetime.now() - timedelta(days=90)
            ).strftime("%Y-%m-%d"))
            if s is not None and len(s) > 0:
                # Get latest non-NaN value
                latest = s.dropna()
                if len(latest) > 0:
                    data[name] = float(latest.iloc[-1])
                    # Also get value from 1 week ago for trend
                    if len(latest) >= 5:
                        data[f"{name}_1w_ago"] = float(latest.iloc[-5])
        except Exception as e:
            log.debug(f"FRED {series_id} failed: {e}")

    return data


async def fetch_vix() -> dict:
    """Fetch VIX from Yahoo Finance."""
    try:
        import yfinance as yf
        vix = yf.Ticker("^VIX")
        hist = vix.history(period="1mo")
        if hist is None or hist.empty:
            return {}

        current = float(hist["Close"].iloc[-1])
        result = {"vix": current}

        # 1-week change
        if len(hist) >= 5:
            week_ago = float(hist["Close"].iloc[-5])
            result["vix_1w_ago"] = week_ago

        return result
    except Exception as e:
        log.warning(f"VIX fetch failed: {e}")
        return {}


async def fetch_yields_yfinance() -> dict:
    """Fallback: fetch treasury yields from Yahoo Finance if FRED unavailable."""
    try:
        import yfinance as yf

        data = {}
        symbols = {
            "yield_10y": "^TNX",   # 10-Year Treasury Note Yield
            "yield_30y": "^TYX",   # 30-Year Treasury Bond Yield
            "yield_2y": "^IRX",    # 13-Week T-Bill (proxy, 2Y not directly available)
        }

        for name, sym in symbols.items():
            try:
                t = yf.Ticker(sym)
                hist = t.history(period="1mo")
                if hist is not None and not hist.empty:
                    data[name] = float(hist["Close"].iloc[-1])
                    if len(hist) >= 5:
                        data[f"{name}_1w_ago"] = float(hist["Close"].iloc[-5])
            except Exception:
                pass

        return data
    except ImportError:
        return {}


async def fetch_put_call_ratio(symbol: str = "SPY") -> float | None:
    """Compute put/call ratio from options chain.

    Uses total open interest across all expiry dates for the given symbol.
    SPY is used as a proxy for market-wide sentiment.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        expiries = ticker.options
        if not expiries:
            return None

        total_put_oi = 0
        total_call_oi = 0

        # Check first 3 expiry dates (near-term = most informative)
        for exp in expiries[:3]:
            try:
                chain = ticker.option_chain(exp)
                total_call_oi += chain.calls["openInterest"].sum()
                total_put_oi += chain.puts["openInterest"].sum()
            except Exception:
                continue

        if total_call_oi > 0:
            return round(total_put_oi / total_call_oi, 3)
        return None
    except Exception as e:
        log.debug(f"Put/call ratio failed: {e}")
        return None


async def get_macro_snapshot() -> MacroSnapshot:
    """Build a complete macro snapshot from all sources."""
    import asyncio

    snap = MacroSnapshot(
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Fetch everything concurrently
    fred_task = asyncio.create_task(fetch_fred_data())
    vix_task = asyncio.create_task(fetch_vix())
    pc_task = asyncio.create_task(fetch_put_call_ratio())

    fred_data = await fred_task
    vix_data = await vix_task
    pc_ratio = await pc_task

    # If FRED unavailable, fall back to yfinance for yields
    if not fred_data or "yield_10y" not in fred_data:
        yf_yields = await fetch_yields_yfinance()
        fred_data.update(yf_yields)

    # Populate snapshot
    snap.yield_2y = fred_data.get("yield_2y")
    snap.yield_10y = fred_data.get("yield_10y")
    snap.yield_30y = fred_data.get("yield_30y")
    snap.fed_funds_rate = fred_data.get("fed_funds")
    snap.dollar_index = fred_data.get("dollar_index")
    snap.cpi_yoy = fred_data.get("cpi")

    # Yield curve
    if snap.yield_10y is not None and snap.yield_2y is not None:
        snap.yield_curve_spread = round(snap.yield_10y - snap.yield_2y, 3)
        if snap.yield_curve_spread < -0.1:
            snap.yield_curve_signal = "inverted"
            snap.signals.append(
                f"Yield curve inverted ({snap.yield_curve_spread:+.2f}%) — "
                f"historically precedes recessions"
            )
        elif snap.yield_curve_spread < 0.2:
            snap.yield_curve_signal = "flat"
            snap.signals.append(
                f"Yield curve nearly flat ({snap.yield_curve_spread:+.2f}%) — "
                f"caution, watch for inversion"
            )
        else:
            snap.yield_curve_signal = "normal"

    # Dollar trend
    if snap.dollar_index is not None:
        dollar_1w = fred_data.get("dollar_index_1w_ago")
        if dollar_1w:
            change = snap.dollar_index - dollar_1w
            if change > 0.5:
                snap.dollar_trend = "strengthening"
                snap.signals.append(
                    f"Dollar strengthening ({change:+.2f} over 1 week) — "
                    f"pressure on commodities and EM"
                )
            elif change < -0.5:
                snap.dollar_trend = "weakening"
                snap.signals.append(
                    f"Dollar weakening ({change:+.2f} over 1 week) — "
                    f"tailwind for commodities and multinationals"
                )
            else:
                snap.dollar_trend = "stable"

    # VIX
    if vix_data.get("vix") is not None:
        snap.vix = vix_data["vix"]
        if snap.vix >= 30:
            snap.vix_signal = "extreme_fear"
            snap.signals.append(
                f"VIX at {snap.vix:.1f} — extreme fear. "
                f"Historically a contrarian buy signal, but catching falling knives hurts."
            )
        elif snap.vix >= 25:
            snap.vix_signal = "high_fear"
            snap.signals.append(f"VIX elevated at {snap.vix:.1f} — market is nervous")
        elif snap.vix >= 18:
            snap.vix_signal = "elevated"
        else:
            snap.vix_signal = "low_fear"
            if snap.vix < 13:
                snap.signals.append(
                    f"VIX very low at {snap.vix:.1f} — complacency. "
                    f"Historically precedes volatility spikes."
                )

        # Weekly change
        vix_1w = vix_data.get("vix_1w_ago")
        if vix_1w:
            snap.vix_change_1w = round(snap.vix - vix_1w, 2)
            if abs(snap.vix_change_1w) > 5:
                direction = "spiked" if snap.vix_change_1w > 0 else "dropped"
                snap.signals.append(
                    f"VIX {direction} {abs(snap.vix_change_1w):.1f} pts this week"
                )

    # Fed funds
    if snap.fed_funds_rate is not None:
        if snap.fed_funds_rate > 5.0:
            snap.signals.append(
                f"Fed funds at {snap.fed_funds_rate:.2f}% — restrictive. "
                f"Headwind for growth stocks and real estate."
            )

    # Put/Call ratio
    snap.put_call_ratio = pc_ratio
    if pc_ratio is not None:
        if pc_ratio > 1.2:
            snap.put_call_signal = "bullish"  # contrarian: excessive puts = fear = buy
            snap.signals.append(
                f"SPY put/call ratio {pc_ratio:.2f} — heavy put buying. "
                f"Contrarian bullish signal (crowd is hedging)."
            )
        elif pc_ratio < 0.7:
            snap.put_call_signal = "bearish"  # contrarian: excessive calls = greed = caution
            snap.signals.append(
                f"SPY put/call ratio {pc_ratio:.2f} — heavy call buying. "
                f"Contrarian bearish signal (crowd is greedy)."
            )
        else:
            snap.put_call_signal = "neutral"

    return snap


def macro_to_text(snap: MacroSnapshot) -> str:
    """Convert macro snapshot to LLM-readable text."""
    lines = ["=== MACRO ENVIRONMENT ===\n"]

    # Yields
    yield_parts = []
    if snap.yield_2y is not None:
        yield_parts.append(f"2Y: {snap.yield_2y:.2f}%")
    if snap.yield_10y is not None:
        yield_parts.append(f"10Y: {snap.yield_10y:.2f}%")
    if snap.yield_30y is not None:
        yield_parts.append(f"30Y: {snap.yield_30y:.2f}%")
    if snap.yield_curve_spread is not None:
        yield_parts.append(f"Curve (10Y-2Y): {snap.yield_curve_spread:+.2f}%")
    if yield_parts:
        lines.append("Treasury Yields: " + " | ".join(yield_parts))

    # Fed funds
    if snap.fed_funds_rate is not None:
        lines.append(f"Fed Funds Rate: {snap.fed_funds_rate:.2f}%")

    # Dollar
    if snap.dollar_index is not None:
        lines.append(f"US Dollar Index: {snap.dollar_index:.2f} ({snap.dollar_trend})")

    # VIX
    if snap.vix is not None:
        vix_str = f"VIX: {snap.vix:.1f} ({snap.vix_signal.replace('_', ' ')})"
        if snap.vix_change_1w is not None:
            vix_str += f" | 1-week change: {snap.vix_change_1w:+.1f}"
        lines.append(vix_str)

    # Put/Call
    if snap.put_call_ratio is not None:
        lines.append(f"SPY Put/Call Ratio: {snap.put_call_ratio:.2f} ({snap.put_call_signal})")

    # Signals
    if snap.signals:
        lines.append("\nMacro Signals:")
        for s in snap.signals:
            lines.append(f"  • {s}")

    lines.append("")
    return "\n".join(lines)
