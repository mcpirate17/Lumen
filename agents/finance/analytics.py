"""Finance analytics engine for Lumen.

Computes technical indicators (RSI, MACD, Bollinger, Stochastic, ADX)
and fundamental valuation metrics (P/E, PEG, P/B, EV/EBITDA) to identify
overbought, oversold, and overvalued conditions.

Uses:
  - yfinance for stock/ETF price history + fundamentals
  - ta library for technical indicator computation
  - CoinGecko for crypto price history
  - All free, no API keys required

Designed to run on watchlist symbols + top movers, producing signal
summaries that get injected into market briefs.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

log = logging.getLogger("lumen.finance.analytics")

_UA = "LumenFinanceBot/1.0"


# --- Data classes ---

@dataclass(slots=True)
class TechnicalSignals:
    symbol: str
    price: float
    rsi: float | None = None
    rsi_signal: str = ""          # "overbought" / "oversold" / ""
    macd_signal: str = ""         # "bullish_cross" / "bearish_cross" / ""
    macd_histogram: float = 0.0
    bb_position: float | None = None  # %B: >1 above upper, <0 below lower
    bb_signal: str = ""           # "above_upper" / "below_lower" / ""
    stoch_k: float | None = None
    stoch_signal: str = ""        # "overbought" / "oversold" / ""
    adx: float | None = None
    adx_signal: str = ""          # "strong_trend" / "weak_trend" / ""
    trend_direction: str = ""     # "bullish" / "bearish" / "sideways"
    sma_50: float | None = None
    sma_200: float | None = None
    golden_death_cross: str = ""  # "golden_cross" / "death_cross" / ""
    # Volume indicators
    mfi: float | None = None      # Money Flow Index (0-100, volume-weighted RSI)
    mfi_signal: str = ""          # "overbought" / "oversold" / ""
    obv_trend: str = ""           # "accumulation" / "distribution" / ""
    cmf: float | None = None      # Chaikin Money Flow (-1 to 1)
    cmf_signal: str = ""          # "buying_pressure" / "selling_pressure" / ""
    # Volatility
    atr: float | None = None      # Average True Range (absolute)
    atr_pct: float | None = None  # ATR as % of price
    # Relative performance
    vs_spy_1m: float | None = None   # relative return vs SPY, 1 month
    vs_spy_3m: float | None = None   # relative return vs SPY, 3 months


@dataclass(slots=True)
class MarketIntelligence:
    """Analyst ratings, insider activity, short interest, earnings."""
    symbol: str
    # Analyst consensus
    analyst_rating: str = ""       # "strong_buy" / "buy" / "hold" / "sell" / "strong_sell"
    analyst_count: int = 0
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    upside_pct: float | None = None  # (target_mean - price) / price
    # Short interest
    short_pct_float: float | None = None
    short_ratio: float | None = None  # days to cover
    short_trend: str = ""          # "increasing" / "decreasing" / ""
    # Insider activity
    insider_buy_count: int = 0
    insider_sell_count: int = 0
    insider_net: str = ""          # "net_buying" / "net_selling" / "neutral"
    institutional_pct: float | None = None
    # Earnings
    next_earnings: str = ""        # date string
    last_earnings_surprise: float | None = None  # % surprise
    # Dividends
    payout_ratio: float | None = None
    dividend_yield: float | None = None
    signals: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FundamentalSignals:
    symbol: str
    name: str = ""
    trailing_pe: float | None = None
    forward_pe: float | None = None
    peg_ratio: float | None = None
    price_to_book: float | None = None
    ev_to_ebitda: float | None = None
    debt_to_equity: float | None = None
    profit_margin: float | None = None
    roe: float | None = None
    beta: float | None = None
    dividend_yield: float | None = None
    fifty_two_week_pct: float | None = None  # position in 52w range (0-1)
    signals: list[str] = field(default_factory=list)  # human-readable signals


@dataclass(slots=True)
class AnalysisResult:
    symbol: str
    asset_type: str  # "stock", "crypto", "etf", "bond", "commodity", "currency"
    timestamp: str = ""
    technical: TechnicalSignals | None = None
    fundamental: FundamentalSignals | None = None
    intelligence: MarketIntelligence | None = None
    summary_signals: list[str] = field(default_factory=list)
    risk_score: float = 0.5  # 0 = low risk, 1 = high risk
    opportunity_score: float = 0.5  # 0 = low opportunity, 1 = high
    plain_language: str = ""  # LLM-generated explanation (filled by storyboard)


# --- Technical analysis (stocks + crypto) ---

def compute_technicals(symbol: str, df) -> TechnicalSignals | None:
    """Compute technical indicators from an OHLCV DataFrame.

    Args:
        symbol: Ticker symbol
        df: pandas DataFrame with columns: Open, High, Low, Close, Volume
            Must have at least 35 rows (for MACD convergence).

    Returns:
        TechnicalSignals or None if insufficient data.
    """
    try:
        from ta.momentum import RSIIndicator, StochasticOscillator
        from ta.trend import MACD, ADXIndicator, SMAIndicator
        from ta.volatility import BollingerBands, AverageTrueRange
        from ta.volume import MFIIndicator, OnBalanceVolumeIndicator, ChaikinMoneyFlowIndicator
    except ImportError:
        log.warning("ta library not installed. Run: pip install ta")
        return None

    if len(df) < 35:
        log.debug(f"{symbol}: insufficient data ({len(df)} rows, need 35+)")
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    price = float(close.iloc[-1])

    sig = TechnicalSignals(symbol=symbol, price=price)

    # RSI (14-period)
    rsi_ind = RSIIndicator(close=close, window=14)
    rsi_vals = rsi_ind.rsi()
    if not rsi_vals.empty and rsi_vals.notna().any():
        sig.rsi = round(float(rsi_vals.iloc[-1]), 2)
        if sig.rsi > 70:
            sig.rsi_signal = "overbought"
        elif sig.rsi < 30:
            sig.rsi_signal = "oversold"

    # MACD (12, 26, 9)
    macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_ind.macd()
    signal_line = macd_ind.macd_signal()
    histogram = macd_ind.macd_diff()
    if len(macd_line) >= 2 and macd_line.notna().iloc[-1] and signal_line.notna().iloc[-1]:
        sig.macd_histogram = round(float(histogram.iloc[-1]), 4)
        # Check for crossover in last 2 bars
        prev_diff = float(macd_line.iloc[-2]) - float(signal_line.iloc[-2])
        curr_diff = float(macd_line.iloc[-1]) - float(signal_line.iloc[-1])
        if prev_diff < 0 and curr_diff > 0:
            sig.macd_signal = "bullish_cross"
        elif prev_diff > 0 and curr_diff < 0:
            sig.macd_signal = "bearish_cross"

    # Bollinger Bands (20, 2)
    bb_ind = BollingerBands(close=close, window=20, window_dev=2)
    bb_high = bb_ind.bollinger_hband()
    bb_low = bb_ind.bollinger_lband()
    if bb_high.notna().iloc[-1] and bb_low.notna().iloc[-1]:
        upper = float(bb_high.iloc[-1])
        lower = float(bb_low.iloc[-1])
        if upper != lower:
            pct_b = (price - lower) / (upper - lower)
            sig.bb_position = round(pct_b, 3)
            if pct_b > 1.0:
                sig.bb_signal = "above_upper"
            elif pct_b < 0.0:
                sig.bb_signal = "below_lower"

    # Stochastic Oscillator (14, 3)
    stoch_ind = StochasticOscillator(high=high, low=low, close=close, window=14, smooth_window=3)
    stoch_k = stoch_ind.stoch()
    if stoch_k.notna().iloc[-1]:
        sig.stoch_k = round(float(stoch_k.iloc[-1]), 2)
        if sig.stoch_k > 80:
            sig.stoch_signal = "overbought"
        elif sig.stoch_k < 20:
            sig.stoch_signal = "oversold"

    # ADX (14-period) — trend strength
    adx_ind = ADXIndicator(high=high, low=low, close=close, window=14)
    adx_val = adx_ind.adx()
    plus_di = adx_ind.adx_pos()
    minus_di = adx_ind.adx_neg()
    if adx_val.notna().iloc[-1]:
        sig.adx = round(float(adx_val.iloc[-1]), 2)
        if sig.adx > 25:
            sig.adx_signal = "strong_trend"
        else:
            sig.adx_signal = "weak_trend"
        # Trend direction from +DI / -DI
        if plus_di.notna().iloc[-1] and minus_di.notna().iloc[-1]:
            if float(plus_di.iloc[-1]) > float(minus_di.iloc[-1]):
                sig.trend_direction = "bullish"
            else:
                sig.trend_direction = "bearish"

    # Moving averages (50 / 200)
    if len(close) >= 50:
        sma50 = SMAIndicator(close=close, window=50).sma_indicator()
        sig.sma_50 = round(float(sma50.iloc[-1]), 2) if sma50.notna().iloc[-1] else None

    if len(close) >= 200:
        sma200 = SMAIndicator(close=close, window=200).sma_indicator()
        sig.sma_200 = round(float(sma200.iloc[-1]), 2) if sma200.notna().iloc[-1] else None

        # Golden / Death cross detection (last 5 bars)
        if sig.sma_50 is not None and len(sma50) >= 5 and len(sma200) >= 5:
            for i in range(-5, -1):
                try:
                    prev_50 = float(sma50.iloc[i])
                    prev_200 = float(sma200.iloc[i])
                    curr_50 = float(sma50.iloc[i + 1])
                    curr_200 = float(sma200.iloc[i + 1])
                    if prev_50 < prev_200 and curr_50 > curr_200:
                        sig.golden_death_cross = "golden_cross"
                    elif prev_50 > prev_200 and curr_50 < curr_200:
                        sig.golden_death_cross = "death_cross"
                except (IndexError, ValueError):
                    pass

    # --- Volume indicators ---
    volume = df["Volume"]
    has_volume = volume.notna().any() and (volume > 0).any()

    if has_volume:
        # Money Flow Index (volume-weighted RSI)
        try:
            mfi_ind = MFIIndicator(high=high, low=low, close=close, volume=volume, window=14)
            mfi_val = mfi_ind.money_flow_index()
            if mfi_val.notna().iloc[-1]:
                sig.mfi = round(float(mfi_val.iloc[-1]), 2)
                if sig.mfi > 80:
                    sig.mfi_signal = "overbought"
                elif sig.mfi < 20:
                    sig.mfi_signal = "oversold"
        except Exception:
            pass

        # On-Balance Volume trend (compare OBV slope over last 20 bars)
        try:
            obv = OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()
            if len(obv) >= 20 and obv.notna().iloc[-1] and obv.notna().iloc[-20]:
                obv_now = float(obv.iloc[-1])
                obv_prev = float(obv.iloc[-20])
                if obv_now > obv_prev * 1.05:
                    sig.obv_trend = "accumulation"
                elif obv_now < obv_prev * 0.95:
                    sig.obv_trend = "distribution"
        except Exception:
            pass

        # Chaikin Money Flow
        try:
            cmf_ind = ChaikinMoneyFlowIndicator(high=high, low=low, close=close, volume=volume, window=20)
            cmf_val = cmf_ind.chaikin_money_flow()
            if cmf_val.notna().iloc[-1]:
                sig.cmf = round(float(cmf_val.iloc[-1]), 4)
                if sig.cmf > 0.1:
                    sig.cmf_signal = "buying_pressure"
                elif sig.cmf < -0.1:
                    sig.cmf_signal = "selling_pressure"
        except Exception:
            pass

    # ATR — volatility
    try:
        atr_ind = AverageTrueRange(high=high, low=low, close=close, window=14)
        atr_val = atr_ind.average_true_range()
        if atr_val.notna().iloc[-1]:
            sig.atr = round(float(atr_val.iloc[-1]), 4)
            sig.atr_pct = round(sig.atr / price * 100, 2) if price > 0 else None
    except Exception:
        pass

    return sig


def compute_relative_performance(symbol: str, df) -> tuple[float | None, float | None]:
    """Compute relative return vs SPY over 1 and 3 months."""
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY").history(period="6mo")
        if spy is None or len(spy) < 63 or len(df) < 63:
            return None, None

        # 1-month (~21 trading days)
        stock_1m = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-21]) - 1) * 100
        spy_1m = (float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[-21]) - 1) * 100
        rel_1m = round(stock_1m - spy_1m, 2)

        # 3-month (~63 trading days)
        stock_3m = (float(df["Close"].iloc[-1]) / float(df["Close"].iloc[-63]) - 1) * 100
        spy_3m = (float(spy["Close"].iloc[-1]) / float(spy["Close"].iloc[-63]) - 1) * 100
        rel_3m = round(stock_3m - spy_3m, 2)

        return rel_1m, rel_3m
    except Exception:
        return None, None


# --- Fundamental analysis (stocks only) ---

def compute_fundamentals(symbol: str) -> FundamentalSignals | None:
    """Fetch and analyze fundamental data from Yahoo Finance via yfinance.

    Returns FundamentalSignals with human-readable signal list.
    """
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed. Run: pip install yfinance")
        return None

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info or info.get("regularMarketPrice") is None:
            return None
    except Exception as e:
        log.warning(f"yfinance {symbol} failed: {e}")
        return None

    sig = FundamentalSignals(
        symbol=symbol,
        name=(info.get("longName") or info.get("shortName") or symbol)[:40],
    )

    # Extract metrics
    sig.trailing_pe = info.get("trailingPE")
    sig.forward_pe = info.get("forwardPE")
    sig.peg_ratio = info.get("pegRatio")
    sig.price_to_book = info.get("priceToBook")
    sig.ev_to_ebitda = info.get("enterpriseToEbitda")
    sig.debt_to_equity = info.get("debtToEquity")
    sig.profit_margin = info.get("profitMargins")
    sig.roe = info.get("returnOnEquity")
    sig.beta = info.get("beta")
    sig.dividend_yield = info.get("dividendYield")

    # 52-week position
    low_52 = info.get("fiftyTwoWeekLow")
    high_52 = info.get("fiftyTwoWeekHigh")
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if low_52 and high_52 and price and high_52 != low_52:
        sig.fifty_two_week_pct = round((price - low_52) / (high_52 - low_52), 3)

    # Compute PEG manually if missing
    if sig.peg_ratio is None and sig.trailing_pe is not None:
        eg = info.get("earningsGrowth")
        if eg and eg > 0:
            sig.peg_ratio = round(sig.trailing_pe / (eg * 100), 2)

    # Generate signals
    signals = sig.signals

    if sig.trailing_pe is not None:
        if sig.trailing_pe > 30:
            signals.append(f"High P/E ({sig.trailing_pe:.1f}) — potentially overvalued")
        elif sig.trailing_pe < 12:
            signals.append(f"Low P/E ({sig.trailing_pe:.1f}) — potential value play")

    if sig.peg_ratio is not None:
        if sig.peg_ratio > 2.0:
            signals.append(f"High PEG ({sig.peg_ratio:.2f}) — overvalued relative to growth")
        elif sig.peg_ratio < 1.0:
            signals.append(f"Low PEG ({sig.peg_ratio:.2f}) — undervalued relative to growth")

    if sig.price_to_book is not None:
        if sig.price_to_book < 1.0:
            signals.append(f"Below book value (P/B {sig.price_to_book:.2f}) — deep value")
        elif sig.price_to_book > 10:
            signals.append(f"High P/B ({sig.price_to_book:.2f}) — premium valuation")

    if sig.ev_to_ebitda is not None:
        if sig.ev_to_ebitda > 20:
            signals.append(f"High EV/EBITDA ({sig.ev_to_ebitda:.1f}) — expensive")
        elif sig.ev_to_ebitda < 8:
            signals.append(f"Low EV/EBITDA ({sig.ev_to_ebitda:.1f}) — potential bargain")

    if sig.debt_to_equity is not None and sig.debt_to_equity > 200:
        signals.append(f"High leverage (D/E {sig.debt_to_equity:.0f}) — elevated risk")

    if sig.roe is not None:
        if sig.roe > 0.20:
            signals.append(f"Strong ROE ({sig.roe:.0%}) — efficient capital use")
        elif sig.roe < 0.05:
            signals.append(f"Weak ROE ({sig.roe:.0%}) — poor capital efficiency")

    if sig.fifty_two_week_pct is not None:
        if sig.fifty_two_week_pct > 0.95:
            signals.append("Near 52-week high — momentum or overextended")
        elif sig.fifty_two_week_pct < 0.1:
            signals.append("Near 52-week low — beaten down, potential value or falling knife")

    if sig.beta is not None and sig.beta > 1.5:
        signals.append(f"High beta ({sig.beta:.2f}) — volatile, amplifies market moves")

    return sig


def compute_market_intelligence(symbol: str) -> MarketIntelligence | None:
    """Fetch analyst ratings, short interest, insider activity, earnings from yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return None

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        if not info:
            return None
    except Exception as e:
        log.warning(f"Market intelligence for {symbol} failed: {e}")
        return None

    price = info.get("regularMarketPrice") or info.get("currentPrice") or 0
    sig = MarketIntelligence(symbol=symbol)
    signals = sig.signals

    # Analyst ratings
    sig.analyst_rating = info.get("recommendationKey", "")
    rec_mean = info.get("recommendationMean")
    sig.analyst_count = info.get("numberOfAnalystOpinions", 0)
    sig.target_mean = info.get("targetMeanPrice")
    sig.target_high = info.get("targetHighPrice")
    sig.target_low = info.get("targetLowPrice")
    if sig.target_mean and price > 0:
        sig.upside_pct = round((sig.target_mean - price) / price * 100, 1)
        if sig.upside_pct > 20:
            signals.append(f"Analysts see {sig.upside_pct:+.0f}% upside (target ${sig.target_mean:.0f})")
        elif sig.upside_pct < -10:
            signals.append(f"Analysts see {sig.upside_pct:+.0f}% downside (target ${sig.target_mean:.0f})")

    if sig.analyst_rating in ("strong_buy", "buy") and sig.analyst_count >= 5:
        signals.append(f"Analyst consensus: {sig.analyst_rating.replace('_', ' ')} ({sig.analyst_count} analysts)")
    elif sig.analyst_rating in ("sell", "strong_sell"):
        signals.append(f"Analyst consensus: {sig.analyst_rating.replace('_', ' ')} ({sig.analyst_count} analysts)")

    # Short interest
    sig.short_pct_float = info.get("shortPercentOfFloat")
    sig.short_ratio = info.get("shortRatio")
    shares_short = info.get("sharesShort", 0)
    shares_short_prior = info.get("sharesShortPriorMonth", 0)
    if shares_short and shares_short_prior:
        if shares_short > shares_short_prior * 1.1:
            sig.short_trend = "increasing"
        elif shares_short < shares_short_prior * 0.9:
            sig.short_trend = "decreasing"

    if sig.short_pct_float is not None:
        if sig.short_pct_float > 0.20:
            signals.append(f"High short interest ({sig.short_pct_float:.0%} of float) — squeeze potential or bearish bet")
        elif sig.short_pct_float > 0.10:
            signals.append(f"Elevated short interest ({sig.short_pct_float:.0%} of float)")
    if sig.short_ratio is not None and sig.short_ratio > 5:
        signals.append(f"High days-to-cover ({sig.short_ratio:.1f}) — shorts could get trapped")

    # Insider activity
    try:
        insider_txns = ticker.insider_transactions
        if insider_txns is not None and len(insider_txns) > 0:
            # Count recent buys vs sells
            for _, row in insider_txns.head(20).iterrows():
                txn = str(row.get("Text", "")).lower()
                if "purchase" in txn or "buy" in txn:
                    sig.insider_buy_count += 1
                elif "sale" in txn or "sell" in txn:
                    sig.insider_sell_count += 1
            if sig.insider_buy_count > sig.insider_sell_count + 2:
                sig.insider_net = "net_buying"
                signals.append(f"Insider net buying ({sig.insider_buy_count} buys vs {sig.insider_sell_count} sells)")
            elif sig.insider_sell_count > sig.insider_buy_count + 2:
                sig.insider_net = "net_selling"
                signals.append(f"Insider net selling ({sig.insider_sell_count} sells vs {sig.insider_buy_count} buys)")
    except Exception:
        pass

    # Institutional ownership
    sig.institutional_pct = info.get("heldPercentInstitutions")
    if sig.institutional_pct is not None and sig.institutional_pct > 0.90:
        signals.append(f"Very high institutional ownership ({sig.institutional_pct:.0%})")

    # Earnings
    try:
        cal = ticker.calendar
        if cal is not None and not cal.empty:
            if "Earnings Date" in cal.index:
                sig.next_earnings = str(cal.loc["Earnings Date"].iloc[0])[:10]
                signals.append(f"Next earnings: {sig.next_earnings}")
    except Exception:
        pass

    try:
        eh = ticker.earnings_history
        if eh is not None and len(eh) > 0:
            last = eh.iloc[-1]
            surprise = last.get("surprisePercent")
            if surprise is not None:
                sig.last_earnings_surprise = round(float(surprise) * 100, 1)
                if abs(sig.last_earnings_surprise) > 10:
                    beat_miss = "beat" if sig.last_earnings_surprise > 0 else "missed"
                    signals.append(f"Last earnings {beat_miss} by {abs(sig.last_earnings_surprise):.0f}%")
    except Exception:
        pass

    # Dividends
    sig.payout_ratio = info.get("payoutRatio")
    sig.dividend_yield = info.get("dividendYield")
    if sig.payout_ratio is not None and sig.payout_ratio > 0.8:
        signals.append(f"High payout ratio ({sig.payout_ratio:.0%}) — dividend may be unsustainable")
    if sig.dividend_yield is not None and sig.dividend_yield > 0.05:
        signals.append(f"High dividend yield ({sig.dividend_yield:.1%})")

    return sig if signals else sig


# --- Crypto technical analysis ---

async def fetch_crypto_history(coin_id: str, days: int = 365) -> "pd.DataFrame | None":
    """Fetch OHLCV-like data from CoinGecko for technical analysis.

    CoinGecko free tier gives daily prices. We construct a pseudo-OHLCV
    using daily price + volume data (Open=Close of prior day, High/Low
    approximated from price variance).
    """
    try:
        import pandas as pd
    except ImportError:
        return None

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": _UA}, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning(f"CoinGecko history for {coin_id} failed: {e}")
            return None

    prices = data.get("prices", [])
    volumes = data.get("total_volumes", [])
    if len(prices) < 35:
        return None

    df = pd.DataFrame(prices, columns=["timestamp", "Close"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)

    # Approximate OHLCV from daily close prices
    df["Open"] = df["Close"].shift(1)
    df["High"] = df[["Open", "Close"]].max(axis=1) * 1.005  # ~0.5% spread approx
    df["Low"] = df[["Open", "Close"]].min(axis=1) * 0.995

    if volumes:
        vol_df = pd.DataFrame(volumes, columns=["timestamp", "Volume"])
        vol_df["timestamp"] = pd.to_datetime(vol_df["timestamp"], unit="ms")
        vol_df.set_index("timestamp", inplace=True)
        df = df.join(vol_df, how="left")
    else:
        df["Volume"] = 0

    df.dropna(inplace=True)
    return df


# --- Combined analysis ---

async def analyze_stock(symbol: str, deep: bool = False) -> AnalysisResult:
    """Run technical + fundamental analysis on a stock/ETF.

    Args:
        symbol: Ticker symbol
        deep: If True, also fetch market intelligence (analyst ratings,
              short interest, insider activity, earnings) and relative
              performance vs SPY. Slower due to extra API calls.
    """
    result = AnalysisResult(
        symbol=symbol.upper(),
        asset_type="stock",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    try:
        import yfinance as yf
        import pandas as pd
    except ImportError:
        log.warning("yfinance/pandas not installed")
        return result

    # Fetch price history (1 year for 200-day SMA)
    df = None
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="1y")
        if df is not None and len(df) >= 35:
            result.technical = compute_technicals(symbol, df)
    except Exception as e:
        log.warning(f"Price history for {symbol} failed: {e}")

    # Fundamentals
    result.fundamental = compute_fundamentals(symbol)

    # Deep analysis: market intelligence + relative performance
    if deep:
        result.intelligence = compute_market_intelligence(symbol)
        if df is not None and len(df) >= 63 and result.technical:
            rel_1m, rel_3m = compute_relative_performance(symbol, df)
            result.technical.vs_spy_1m = rel_1m
            result.technical.vs_spy_3m = rel_3m

    # Aggregate signals and scores
    _score_result(result)
    return result


async def analyze_crypto(coin_id: str) -> AnalysisResult:
    """Run technical analysis on a cryptocurrency."""
    result = AnalysisResult(
        symbol=coin_id.upper(),
        asset_type="crypto",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    df = await fetch_crypto_history(coin_id)
    if df is not None and len(df) >= 35:
        result.technical = compute_technicals(coin_id.upper(), df)

    _score_result(result)
    return result


async def analyze_watchlist(watchlist: list[dict]) -> list[AnalysisResult]:
    """Analyze all symbols in the user's watchlist concurrently."""
    if not watchlist:
        return []

    tasks = []
    for item in watchlist:
        sym = item["symbol"]
        atype = item.get("asset_type", "stock")
        if atype == "crypto":
            tasks.append(analyze_crypto(sym.lower()))
        else:
            tasks.append(analyze_stock(sym))

    return await asyncio.gather(*tasks, return_exceptions=False)


async def analyze_top_movers(symbols: list[str], asset_type: str = "stock") -> list[AnalysisResult]:
    """Analyze a list of symbols (e.g., top gainers/losers from collector)."""
    tasks = []
    for sym in symbols[:10]:  # cap at 10 to avoid rate limits
        if asset_type == "crypto":
            tasks.append(analyze_crypto(sym.lower()))
        else:
            tasks.append(analyze_stock(sym))
    return await asyncio.gather(*tasks, return_exceptions=False)


# --- Scoring ---

def _score_result(result: AnalysisResult):
    """Compute risk + opportunity scores and generate summary signals."""
    signals = []
    risk = 0.5
    opportunity = 0.5

    t = result.technical
    f = result.fundamental

    if t:
        # Technical risk/opportunity signals
        if t.rsi_signal == "overbought":
            signals.append(f"RSI {t.rsi:.0f} — overbought")
            risk += 0.15
            opportunity -= 0.1
        elif t.rsi_signal == "oversold":
            signals.append(f"RSI {t.rsi:.0f} — oversold, potential bounce")
            opportunity += 0.15
            risk += 0.05

        if t.macd_signal == "bullish_cross":
            signals.append("MACD bullish crossover")
            opportunity += 0.1
        elif t.macd_signal == "bearish_cross":
            signals.append("MACD bearish crossover")
            risk += 0.1

        if t.bb_signal == "above_upper":
            signals.append(f"Above Bollinger upper band (%B={t.bb_position:.2f})")
            risk += 0.1
        elif t.bb_signal == "below_lower":
            signals.append(f"Below Bollinger lower band (%B={t.bb_position:.2f})")
            opportunity += 0.1

        if t.stoch_signal == "overbought":
            signals.append(f"Stochastic {t.stoch_k:.0f} — overbought")
            risk += 0.1
        elif t.stoch_signal == "oversold":
            signals.append(f"Stochastic {t.stoch_k:.0f} — oversold")
            opportunity += 0.1

        if t.adx_signal == "strong_trend":
            qualifier = "bullish" if t.trend_direction == "bullish" else "bearish"
            signals.append(f"Strong {qualifier} trend (ADX {t.adx:.0f})")
            if t.trend_direction == "bearish":
                risk += 0.1
            else:
                opportunity += 0.1

        if t.golden_death_cross == "golden_cross":
            signals.append("Golden cross (50 SMA > 200 SMA) — bullish long-term")
            opportunity += 0.15
        elif t.golden_death_cross == "death_cross":
            signals.append("Death cross (50 SMA < 200 SMA) — bearish long-term")
            risk += 0.15

    if f:
        signals.extend(f.signals)
        # Adjust scores from fundamentals
        if f.peg_ratio is not None:
            if f.peg_ratio > 2.0:
                risk += 0.1
            elif f.peg_ratio < 1.0:
                opportunity += 0.1
        if f.debt_to_equity is not None and f.debt_to_equity > 200:
            risk += 0.1
        if f.fifty_two_week_pct is not None:
            if f.fifty_two_week_pct > 0.95:
                risk += 0.05
            elif f.fifty_two_week_pct < 0.1:
                opportunity += 0.05
                risk += 0.05  # could be falling knife

    # Volume-based signals
    if t:
        if t.mfi_signal == "overbought":
            signals.append(f"MFI {t.mfi:.0f} — money flow overbought")
            risk += 0.05
        elif t.mfi_signal == "oversold":
            signals.append(f"MFI {t.mfi:.0f} — money flow oversold")
            opportunity += 0.05
        if t.obv_trend == "accumulation":
            signals.append("OBV rising — accumulation (smart money buying)")
            opportunity += 0.05
        elif t.obv_trend == "distribution":
            signals.append("OBV falling — distribution (smart money selling)")
            risk += 0.05
        if t.cmf_signal == "buying_pressure":
            signals.append(f"Chaikin MF {t.cmf:+.3f} — buying pressure")
            opportunity += 0.03
        elif t.cmf_signal == "selling_pressure":
            signals.append(f"Chaikin MF {t.cmf:+.3f} — selling pressure")
            risk += 0.03
        if t.vs_spy_1m is not None:
            if t.vs_spy_1m > 10:
                signals.append(f"Outperforming SPY by {t.vs_spy_1m:+.1f}% (1 month)")
            elif t.vs_spy_1m < -10:
                signals.append(f"Underperforming SPY by {t.vs_spy_1m:+.1f}% (1 month)")

    # Market intelligence signals
    mi = result.intelligence
    if mi:
        signals.extend(mi.signals)
        if mi.upside_pct is not None and mi.upside_pct > 20:
            opportunity += 0.1
        elif mi.upside_pct is not None and mi.upside_pct < -10:
            risk += 0.1
        if mi.short_pct_float is not None and mi.short_pct_float > 0.20:
            risk += 0.05
            opportunity += 0.05  # squeeze potential
        if mi.insider_net == "net_buying":
            opportunity += 0.05
        elif mi.insider_net == "net_selling":
            risk += 0.05

    result.summary_signals = signals
    result.risk_score = round(min(1.0, max(0.0, risk)), 3)
    result.opportunity_score = round(min(1.0, max(0.0, opportunity)), 3)


# --- Text output for LLM injection ---

def analysis_to_text(results: list[AnalysisResult]) -> str:
    """Convert analysis results to LLM-readable plain text."""
    if not results:
        return ""

    lines = ["=== TECHNICAL & FUNDAMENTAL ANALYSIS ===\n"]

    for r in results:
        if not r.summary_signals:
            continue

        header = f"--- {r.symbol} ({r.asset_type.upper()}) ---"
        lines.append(header)

        if r.technical:
            t = r.technical
            parts = [f"Price: ${t.price:,.2f}"]
            if t.rsi is not None:
                parts.append(f"RSI: {t.rsi:.0f}")
            if t.mfi is not None:
                parts.append(f"MFI: {t.mfi:.0f}")
            if t.stoch_k is not None:
                parts.append(f"Stoch: {t.stoch_k:.0f}")
            if t.adx is not None:
                parts.append(f"ADX: {t.adx:.0f}")
            if t.bb_position is not None:
                parts.append(f"BB%B: {t.bb_position:.2f}")
            if t.sma_50 is not None:
                parts.append(f"SMA50: ${t.sma_50:,.2f}")
            if t.sma_200 is not None:
                parts.append(f"SMA200: ${t.sma_200:,.2f}")
            lines.append("  " + " | ".join(parts))

            # Volume line
            vol_parts = []
            if t.obv_trend:
                vol_parts.append(f"OBV: {t.obv_trend}")
            if t.cmf is not None:
                vol_parts.append(f"CMF: {t.cmf:+.3f}")
            if t.atr_pct is not None:
                vol_parts.append(f"ATR: {t.atr_pct:.1f}%")
            if t.vs_spy_1m is not None:
                vol_parts.append(f"vs SPY 1m: {t.vs_spy_1m:+.1f}%")
            if t.vs_spy_3m is not None:
                vol_parts.append(f"3m: {t.vs_spy_3m:+.1f}%")
            if vol_parts:
                lines.append("  " + " | ".join(vol_parts))

        if r.fundamental and r.fundamental.trailing_pe is not None:
            f = r.fundamental
            parts = []
            if f.trailing_pe is not None:
                parts.append(f"P/E: {f.trailing_pe:.1f}")
            if f.peg_ratio is not None:
                parts.append(f"PEG: {f.peg_ratio:.2f}")
            if f.price_to_book is not None:
                parts.append(f"P/B: {f.price_to_book:.2f}")
            if f.ev_to_ebitda is not None:
                parts.append(f"EV/EBITDA: {f.ev_to_ebitda:.1f}")
            if f.roe is not None:
                parts.append(f"ROE: {f.roe:.0%}")
            if f.fifty_two_week_pct is not None:
                parts.append(f"52w: {f.fifty_two_week_pct:.0%}")
            if parts:
                lines.append("  " + " | ".join(parts))

        # Market intelligence line
        if r.intelligence:
            mi = r.intelligence
            mi_parts = []
            if mi.analyst_rating:
                mi_parts.append(f"Analyst: {mi.analyst_rating.replace('_', ' ')}")
            if mi.upside_pct is not None:
                mi_parts.append(f"Target upside: {mi.upside_pct:+.0f}%")
            if mi.short_pct_float is not None:
                mi_parts.append(f"Short: {mi.short_pct_float:.1%}")
            if mi.insider_net:
                mi_parts.append(f"Insiders: {mi.insider_net.replace('_', ' ')}")
            if mi_parts:
                lines.append("  " + " | ".join(mi_parts))

        # Risk/Opportunity summary
        risk_label = "HIGH" if r.risk_score > 0.7 else "MODERATE" if r.risk_score > 0.5 else "LOW"
        opp_label = "HIGH" if r.opportunity_score > 0.7 else "MODERATE" if r.opportunity_score > 0.5 else "LOW"
        lines.append(f"  Risk: {risk_label} ({r.risk_score:.2f}) | Opportunity: {opp_label} ({r.opportunity_score:.2f})")

        # Signals
        lines.append("  Signals:")
        for s in r.summary_signals:
            lines.append(f"    • {s}")
        lines.append("")

    return "\n".join(lines)


def signals_summary(results: list[AnalysisResult]) -> dict:
    """Produce a structured summary for programmatic use.

    Returns dict with overbought, oversold, overvalued, undervalued,
    high_risk, high_opportunity lists.
    """
    summary = {
        "overbought": [],
        "oversold": [],
        "overvalued": [],
        "undervalued": [],
        "high_risk": [],
        "high_opportunity": [],
        "notable_signals": [],
    }

    for r in results:
        sym = r.symbol
        t = r.technical
        f = r.fundamental

        if t:
            # Overbought: RSI > 70 OR Stoch > 80 OR above Bollinger upper
            ob_count = sum([
                t.rsi_signal == "overbought",
                t.stoch_signal == "overbought",
                t.bb_signal == "above_upper",
            ])
            if ob_count >= 2:
                summary["overbought"].append({"symbol": sym, "indicators": ob_count,
                                               "rsi": t.rsi, "stoch": t.stoch_k})
            # Oversold: RSI < 30 OR Stoch < 20 OR below Bollinger lower
            os_count = sum([
                t.rsi_signal == "oversold",
                t.stoch_signal == "oversold",
                t.bb_signal == "below_lower",
            ])
            if os_count >= 2:
                summary["oversold"].append({"symbol": sym, "indicators": os_count,
                                             "rsi": t.rsi, "stoch": t.stoch_k})

        if f:
            # Overvalued: high P/E AND high PEG
            overvalued = (
                (f.trailing_pe is not None and f.trailing_pe > 30) and
                (f.peg_ratio is not None and f.peg_ratio > 2.0)
            )
            if overvalued:
                summary["overvalued"].append({"symbol": sym, "pe": f.trailing_pe,
                                               "peg": f.peg_ratio})

            # Undervalued: low PEG OR below book value
            undervalued = (
                (f.peg_ratio is not None and f.peg_ratio < 1.0) or
                (f.price_to_book is not None and f.price_to_book < 1.0)
            )
            if undervalued:
                summary["undervalued"].append({"symbol": sym, "peg": f.peg_ratio,
                                                "pb": f.price_to_book})

        if r.risk_score > 0.7:
            summary["high_risk"].append({"symbol": sym, "score": r.risk_score})
        if r.opportunity_score > 0.7:
            summary["high_opportunity"].append({"symbol": sym, "score": r.opportunity_score})

        # Notable individual signals (MACD crosses, golden/death cross)
        if t and t.macd_signal:
            summary["notable_signals"].append(f"{sym}: {t.macd_signal.replace('_', ' ')}")
        if t and t.golden_death_cross:
            summary["notable_signals"].append(f"{sym}: {t.golden_death_cross.replace('_', ' ')}")

    return summary
