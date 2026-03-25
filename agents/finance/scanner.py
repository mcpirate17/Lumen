"""Automated market scanner for Lumen.

Runs on a cron schedule (or on-demand) to scan the market for interesting
signals: overbought/oversold, overvalued, momentum shifts, volume anomalies,
earnings events, insider activity.

Scans three tiers:
  1. Quick scan (~30s): watchlist + sector ETFs + top crypto — runs every 30 min
  2. Full scan (~5 min): entire scan_universe with deep analysis — runs every 4 hours
  3. Watchlist scan (~15s): just watchlist items — runs every 15 min

Results stored in scan_findings table. The finance tab shows unseen findings.
Lumen can proactively mention notable findings during conversation.
"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone

log = logging.getLogger("lumen.finance.scanner")


async def quick_scan() -> dict:
    """Quick scan: sector ETFs + watchlist + top crypto.

    Runs in ~30s. Designed for every-30-minute cron.
    Uses basic technical analysis only (no deep/intelligence).
    """
    return await _run_scan("quick", deep=False, sources=("sector_etf", "crypto_top", "watchlist"))


async def full_scan() -> dict:
    """Full scan: entire universe with deep analysis.

    Runs in ~5 min. Designed for every-4-hour cron.
    Includes market intelligence (analyst ratings, short interest, etc).
    """
    return await _run_scan("full", deep=True, sources=None)  # all sources


async def watchlist_scan() -> dict:
    """Watchlist-only scan. Fast, every 15 minutes."""
    return await _run_scan("watchlist", deep=False, sources=("watchlist",))


async def _run_scan(scan_type: str, deep: bool, sources: tuple | None) -> dict:
    """Core scan loop."""
    from server.database import get_db
    from agents.finance.analytics import (
        analyze_stock, analyze_crypto, signals_summary,
    )
    from agents.finance.macro import get_macro_snapshot

    scan_id = f"{scan_type}_{uuid.uuid4().hex[:8]}"
    t0 = time.monotonic()
    log.info(f"[SCAN] Starting {scan_type} scan ({scan_id})")

    # Fetch macro data concurrently with universe lookup
    macro_task = asyncio.create_task(get_macro_snapshot())

    # Get symbols to scan
    db = await get_db()
    try:
        if sources:
            placeholders = ",".join("?" for _ in sources)
            async with db.execute(
                f"SELECT symbol, asset_type, source FROM scan_universe WHERE enabled = 1 AND source IN ({placeholders})",
                sources,
            ) as cur:
                universe = [dict(r) for r in await cur.fetchall()]
        else:
            async with db.execute(
                "SELECT symbol, asset_type, source FROM scan_universe WHERE enabled = 1"
            ) as cur:
                universe = [dict(r) for r in await cur.fetchall()]

        # Also add watchlist items if not already in universe
        if sources is None or "watchlist" in sources:
            async with db.execute("SELECT symbol, asset_type FROM watchlist") as cur:
                for row in await cur.fetchall():
                    sym = row["symbol"]
                    atype = row["asset_type"]
                    if not any(u["symbol"] == sym for u in universe):
                        universe.append({"symbol": sym, "asset_type": atype, "source": "watchlist"})
    finally:
        await db.close()

    if not universe:
        log.info("[SCAN] Empty universe, nothing to scan")
        return {"scan_id": scan_id, "findings": 0, "scanned": 0}

    # Run analysis in batches (avoid rate limiting)
    all_results = []
    batch_size = 5  # concurrent requests per batch

    for i in range(0, len(universe), batch_size):
        batch = universe[i:i + batch_size]
        tasks = []
        for item in batch:
            sym = item["symbol"]
            atype = item["asset_type"]
            if atype == "crypto":
                tasks.append(analyze_crypto(sym))
            else:
                tasks.append(analyze_stock(sym, deep=deep))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, Exception):
                log.debug(f"[SCAN] Analysis failed: {r}")
            else:
                all_results.append(r)

        # Brief pause between batches to avoid rate limits
        if i + batch_size < len(universe):
            await asyncio.sleep(1)

    # Extract findings from results
    findings = _extract_findings(all_results, scan_id)

    # Add macro findings
    try:
        macro = await macro_task
        findings.extend(_extract_macro_findings(macro, scan_id))
    except Exception as e:
        log.debug(f"Macro findings failed: {e}")

    # Store findings in DB
    if findings:
        await _store_findings(findings)

    # Update scan_runs
    elapsed = int((time.monotonic() - t0) * 1000)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO scan_runs (scan_id, scan_type, symbols_scanned, findings_count, duration_ms)
               VALUES (?, ?, ?, ?, ?)""",
            (scan_id, scan_type, len(universe), len(findings), elapsed),
        )

        # Mark symbols as scanned
        now = datetime.now(timezone.utc).isoformat()
        for item in universe:
            await db.execute(
                "UPDATE scan_universe SET last_scanned = ? WHERE symbol = ? AND asset_type = ?",
                (now, item["symbol"], item["asset_type"]),
            )
        await db.commit()
    finally:
        await db.close()

    log.info(
        f"[SCAN] {scan_type} complete: {len(universe)} symbols, "
        f"{len(findings)} findings, {elapsed}ms"
    )

    return {
        "scan_id": scan_id,
        "scan_type": scan_type,
        "scanned": len(universe),
        "findings": len(findings),
        "duration_ms": elapsed,
    }


def _extract_findings(results: list, scan_id: str) -> list[dict]:
    """Extract notable findings from analysis results."""
    findings = []

    for r in results:
        t = r.technical
        f = r.fundamental
        mi = r.intelligence

        # --- Overbought (2+ confirming indicators) ---
        if t:
            ob_indicators = []
            if t.rsi is not None and t.rsi > 70:
                ob_indicators.append(f"RSI {t.rsi:.0f}")
            if t.stoch_k is not None and t.stoch_k > 80:
                ob_indicators.append(f"Stoch {t.stoch_k:.0f}")
            if t.bb_position is not None and t.bb_position > 1.0:
                ob_indicators.append(f"BB%B {t.bb_position:.2f}")
            if t.mfi is not None and t.mfi > 80:
                ob_indicators.append(f"MFI {t.mfi:.0f}")

            if len(ob_indicators) >= 2:
                findings.append(_make_finding(
                    scan_id, r, "overbought",
                    f"{r.symbol} overbought on {len(ob_indicators)} indicators",
                    f"Confirmed by: {', '.join(ob_indicators)}. "
                    f"Price may be extended — watch for pullback.",
                    indicators=ob_indicators,
                ))

            # --- Oversold ---
            os_indicators = []
            if t.rsi is not None and t.rsi < 30:
                os_indicators.append(f"RSI {t.rsi:.0f}")
            if t.stoch_k is not None and t.stoch_k < 20:
                os_indicators.append(f"Stoch {t.stoch_k:.0f}")
            if t.bb_position is not None and t.bb_position < 0.0:
                os_indicators.append(f"BB%B {t.bb_position:.2f}")
            if t.mfi is not None and t.mfi < 20:
                os_indicators.append(f"MFI {t.mfi:.0f}")

            if len(os_indicators) >= 2:
                findings.append(_make_finding(
                    scan_id, r, "oversold",
                    f"{r.symbol} oversold on {len(os_indicators)} indicators",
                    f"Confirmed by: {', '.join(os_indicators)}. "
                    f"Potential bounce candidate — check volume for confirmation.",
                    indicators=os_indicators,
                ))

            # --- Momentum: MACD crossover + strong trend ---
            if t.macd_signal == "bullish_cross" and t.adx_signal == "strong_trend":
                findings.append(_make_finding(
                    scan_id, r, "momentum",
                    f"{r.symbol} bullish MACD crossover with strong trend (ADX {t.adx:.0f})",
                    f"MACD just crossed bullish and ADX confirms strong trend direction. "
                    f"Trend: {t.trend_direction}.",
                    indicators=["MACD bullish cross", f"ADX {t.adx:.0f}"],
                ))
            elif t.macd_signal == "bearish_cross" and t.adx_signal == "strong_trend":
                findings.append(_make_finding(
                    scan_id, r, "reversal",
                    f"{r.symbol} bearish MACD crossover with strong trend (ADX {t.adx:.0f})",
                    f"MACD just crossed bearish. Trend: {t.trend_direction}.",
                    indicators=["MACD bearish cross", f"ADX {t.adx:.0f}"],
                ))

            # --- Golden / Death cross ---
            if t.golden_death_cross == "golden_cross":
                findings.append(_make_finding(
                    scan_id, r, "momentum",
                    f"{r.symbol} golden cross — 50-day SMA crossed above 200-day",
                    "This is a classic long-term bullish signal.",
                    indicators=["Golden cross"],
                ))
            elif t.golden_death_cross == "death_cross":
                findings.append(_make_finding(
                    scan_id, r, "reversal",
                    f"{r.symbol} death cross — 50-day SMA crossed below 200-day",
                    "This is a classic long-term bearish signal.",
                    indicators=["Death cross"],
                ))

            # --- Volume anomaly ---
            if t.obv_trend == "distribution" and t.cmf_signal == "selling_pressure":
                findings.append(_make_finding(
                    scan_id, r, "volume_anomaly",
                    f"{r.symbol} distribution + selling pressure",
                    f"OBV falling (distribution) and Chaikin MF ({t.cmf:+.3f}) confirms selling. "
                    f"Smart money may be exiting.",
                    indicators=["OBV distribution", f"CMF {t.cmf:+.3f}"],
                ))
            elif t.obv_trend == "accumulation" and t.cmf_signal == "buying_pressure":
                findings.append(_make_finding(
                    scan_id, r, "volume_anomaly",
                    f"{r.symbol} accumulation + buying pressure",
                    f"OBV rising and CMF ({t.cmf:+.3f}) confirms buying. Smart money loading up.",
                    indicators=["OBV accumulation", f"CMF {t.cmf:+.3f}"],
                ))

        # --- Overvalued (high P/E + high PEG) ---
        if f and f.trailing_pe is not None and f.peg_ratio is not None:
            if f.trailing_pe > 30 and f.peg_ratio > 2.0:
                findings.append(_make_finding(
                    scan_id, r, "overvalued",
                    f"{r.symbol} overvalued — P/E {f.trailing_pe:.0f}, PEG {f.peg_ratio:.1f}",
                    f"Expensive on both absolute (P/E) and growth-adjusted (PEG) basis.",
                    indicators=[f"P/E {f.trailing_pe:.1f}", f"PEG {f.peg_ratio:.2f}"],
                ))

        # --- Undervalued ---
        if f:
            if (f.peg_ratio is not None and f.peg_ratio < 1.0) or \
               (f.price_to_book is not None and f.price_to_book < 1.0):
                reason = []
                if f.peg_ratio is not None and f.peg_ratio < 1.0:
                    reason.append(f"PEG {f.peg_ratio:.2f}")
                if f.price_to_book is not None and f.price_to_book < 1.0:
                    reason.append(f"P/B {f.price_to_book:.2f}")
                findings.append(_make_finding(
                    scan_id, r, "undervalued",
                    f"{r.symbol} potentially undervalued — {', '.join(reason)}",
                    f"Trading below growth-adjusted or book value. Worth a closer look.",
                    indicators=reason,
                ))

        # --- Market intelligence signals (deep scan only) ---
        if mi:
            if mi.short_pct_float is not None and mi.short_pct_float > 0.20:
                findings.append(_make_finding(
                    scan_id, r, "high_risk",
                    f"{r.symbol} high short interest ({mi.short_pct_float:.0%} of float)",
                    f"Days to cover: {mi.short_ratio:.1f}. "
                    f"Could squeeze or signals serious bearish conviction.",
                    indicators=[f"Short {mi.short_pct_float:.0%}", f"DTC {mi.short_ratio:.1f}"],
                ))

            if mi.insider_net == "net_buying":
                findings.append(_make_finding(
                    scan_id, r, "insider_activity",
                    f"{r.symbol} insider net buying ({mi.insider_buy_count} buys)",
                    f"Insiders buying their own stock — they know something or think it's cheap.",
                    indicators=["Insider buying"],
                ))

            if mi.upside_pct is not None and mi.upside_pct > 30 and mi.analyst_count >= 5:
                findings.append(_make_finding(
                    scan_id, r, "analyst_upgrade",
                    f"{r.symbol} analysts see {mi.upside_pct:+.0f}% upside (${mi.target_mean:.0f} target)",
                    f"Consensus: {mi.analyst_rating}. {mi.analyst_count} analysts covering.",
                    indicators=[f"Target +{mi.upside_pct:.0f}%", mi.analyst_rating],
                ))

            if mi.next_earnings:
                findings.append(_make_finding(
                    scan_id, r, "earnings_event",
                    f"{r.symbol} earnings coming up: {mi.next_earnings}",
                    f"Last surprise: {mi.last_earnings_surprise:+.0f}%" if mi.last_earnings_surprise else "No prior surprise data.",
                    indicators=["Earnings date"],
                ))

        # --- Overall risk/opportunity scores ---
        if r.risk_score > 0.75:
            if not any(fi["category"] in ("overbought", "overvalued", "high_risk") for fi in findings if fi.get("symbol") == r.symbol):
                findings.append(_make_finding(
                    scan_id, r, "high_risk",
                    f"{r.symbol} elevated risk score ({r.risk_score:.2f})",
                    f"Multiple risk signals flagged. {len(r.summary_signals)} total signals.",
                    indicators=[s[:40] for s in r.summary_signals[:3]],
                ))
        if r.opportunity_score > 0.75:
            if not any(fi["category"] in ("oversold", "undervalued", "high_opportunity") for fi in findings if fi.get("symbol") == r.symbol):
                findings.append(_make_finding(
                    scan_id, r, "high_opportunity",
                    f"{r.symbol} high opportunity score ({r.opportunity_score:.2f})",
                    f"Multiple opportunity signals. {len(r.summary_signals)} total signals.",
                    indicators=[s[:40] for s in r.summary_signals[:3]],
                ))

    return findings


def _extract_macro_findings(macro, scan_id: str) -> list[dict]:
    """Extract notable macro findings."""
    findings = []

    def _macro_finding(category, headline, detail, indicators):
        return {
            "scan_id": scan_id,
            "symbol": "MACRO",
            "asset_type": "bond",  # macro signals stored under bond type
            "category": category,
            "headline": headline,
            "detail": detail,
            "risk_score": 0.5,
            "opportunity_score": 0.5,
            "confidence": min(1.0, len(indicators) * 0.3 + 0.1),
            "indicators": json.dumps(indicators),
            "price": None,
        }

    if macro.yield_curve_signal == "inverted":
        findings.append(_macro_finding(
            "high_risk",
            f"Yield curve inverted ({macro.yield_curve_spread:+.2f}%)",
            "The 10Y-2Y spread is negative — historically a recession predictor. "
            "Defensive positioning may be warranted.",
            ["Yield curve inverted", f"Spread {macro.yield_curve_spread:+.2f}%"],
        ))

    if macro.vix is not None and macro.vix >= 30:
        findings.append(_macro_finding(
            "high_opportunity",
            f"VIX at {macro.vix:.0f} — extreme fear",
            "Market fear is extreme. Historically, buying when VIX > 30 has "
            "produced above-average 1-year returns — but timing is hard.",
            [f"VIX {macro.vix:.0f}"],
        ))
    elif macro.vix is not None and macro.vix < 13:
        findings.append(_macro_finding(
            "high_risk",
            f"VIX at {macro.vix:.0f} — extreme complacency",
            "Volatility is very low. Markets tend to mean-revert from complacent levels. "
            "Not a sell signal, but a 'tighten stops' signal.",
            [f"VIX {macro.vix:.0f}"],
        ))

    if macro.put_call_ratio is not None and macro.put_call_ratio > 1.2:
        findings.append(_macro_finding(
            "high_opportunity",
            f"Put/call ratio {macro.put_call_ratio:.2f} — heavy put buying",
            "The crowd is loading up on puts (downside protection). "
            "Contrarian signal: extreme hedging often marks near-term bottoms.",
            [f"P/C {macro.put_call_ratio:.2f}"],
        ))
    elif macro.put_call_ratio is not None and macro.put_call_ratio < 0.7:
        findings.append(_macro_finding(
            "high_risk",
            f"Put/call ratio {macro.put_call_ratio:.2f} — heavy call buying",
            "Excessive bullish options positioning. Contrarian warning: "
            "euphoria often precedes pullbacks.",
            [f"P/C {macro.put_call_ratio:.2f}"],
        ))

    if macro.vix_change_1w is not None and macro.vix_change_1w > 8:
        findings.append(_macro_finding(
            "high_risk",
            f"VIX spiked {macro.vix_change_1w:+.0f} pts this week",
            "Sharp fear increase. Something spooked the market. "
            "Check news for catalyst.",
            [f"VIX +{macro.vix_change_1w:.0f}"],
        ))

    return findings


def _make_finding(scan_id: str, result, category: str, headline: str,
                  detail: str, indicators: list[str] = None) -> dict:
    """Create a finding dict."""
    return {
        "scan_id": scan_id,
        "symbol": result.symbol,
        "asset_type": result.asset_type,
        "category": category,
        "headline": headline,
        "detail": detail,
        "risk_score": result.risk_score,
        "opportunity_score": result.opportunity_score,
        "confidence": min(1.0, len(indicators or []) * 0.3 + 0.1),
        "indicators": json.dumps(indicators or []),
        "price": result.technical.price if result.technical else None,
    }


async def _store_findings(findings: list[dict]):
    """Store findings in the database, deduplicating against recent findings."""
    from server.database import get_db

    db = await get_db()
    try:
        for f in findings:
            # Skip if same symbol+category finding exists from last 2 hours
            async with db.execute(
                """SELECT id FROM scan_findings
                   WHERE symbol = ? AND category = ? AND dismissed = 0
                   AND timestamp >= datetime('now', '-2 hours')""",
                (f["symbol"], f["category"]),
            ) as cur:
                if await cur.fetchone():
                    continue

            await db.execute(
                """INSERT INTO scan_findings
                   (scan_id, symbol, asset_type, category, headline, detail,
                    risk_score, opportunity_score, confidence, indicators, price)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (f["scan_id"], f["symbol"], f["asset_type"], f["category"],
                 f["headline"], f["detail"], f["risk_score"],
                 f["opportunity_score"], f["confidence"],
                 f["indicators"], f["price"]),
            )
        await db.commit()
    finally:
        await db.close()


async def get_dashboard_data(limit: int = 50) -> dict:
    """Get data for the finance tab dashboard.

    Returns unseen findings grouped by category, plus recent scan info.
    """
    from server.database import get_db

    db = await get_db()
    try:
        # Get unseen findings (newest first)
        async with db.execute(
            """SELECT * FROM scan_findings
               WHERE dismissed = 0
               ORDER BY seen ASC, timestamp DESC
               LIMIT ?""",
            (limit,),
        ) as cur:
            findings = [dict(r) for r in await cur.fetchall()]

        # Group by category
        by_category = {}
        for f in findings:
            cat = f["category"]
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(f)

        # Get last scan info
        async with db.execute(
            "SELECT * FROM scan_runs ORDER BY timestamp DESC LIMIT 1"
        ) as cur:
            last_scan = dict(await cur.fetchone()) if (row := await cur.fetchone()) else None

        # Counts
        async with db.execute(
            "SELECT COUNT(*) FROM scan_findings WHERE seen = 0 AND dismissed = 0"
        ) as cur:
            unseen_count = (await cur.fetchone())[0]

        return {
            "findings": findings,
            "by_category": by_category,
            "unseen_count": unseen_count,
            "last_scan": last_scan,
            "categories": list(by_category.keys()),
        }
    finally:
        await db.close()


async def mark_seen(finding_ids: list[int]):
    """Mark findings as seen when user views the finance tab."""
    from server.database import get_db
    if not finding_ids:
        return
    db = await get_db()
    try:
        placeholders = ",".join("?" for _ in finding_ids)
        await db.execute(
            f"UPDATE scan_findings SET seen = 1 WHERE id IN ({placeholders})",
            finding_ids,
        )
        await db.commit()
    finally:
        await db.close()


async def dismiss_finding(finding_id: int):
    """Dismiss a finding (user doesn't want to see it)."""
    from server.database import get_db
    db = await get_db()
    try:
        await db.execute(
            "UPDATE scan_findings SET dismissed = 1 WHERE id = ?",
            (finding_id,),
        )
        await db.commit()
    finally:
        await db.close()


async def add_to_universe(symbol: str, asset_type: str = "stock",
                          source: str = "manual"):
    """Add a symbol to the scan universe."""
    from server.database import get_db
    db = await get_db()
    try:
        await db.execute(
            """INSERT OR IGNORE INTO scan_universe (symbol, asset_type, source)
               VALUES (?, ?, ?)""",
            (symbol, asset_type, source),
        )
        await db.commit()
    finally:
        await db.close()


async def get_proactive_findings(max_items: int = 3) -> list[dict]:
    """Get the most notable unseen findings for proactive mention in conversation.

    Used by the proactive system to say things like:
    "Hey, heads up — NVDA is oversold on 3 indicators and analysts see 25% upside."
    """
    from server.database import get_db
    db = await get_db()
    try:
        async with db.execute(
            """SELECT * FROM scan_findings
               WHERE seen = 0 AND dismissed = 0
               AND confidence >= 0.5
               ORDER BY
                 CASE category
                   WHEN 'oversold' THEN 1
                   WHEN 'overbought' THEN 2
                   WHEN 'momentum' THEN 3
                   WHEN 'insider_activity' THEN 4
                   WHEN 'undervalued' THEN 5
                   WHEN 'overvalued' THEN 6
                   ELSE 10
                 END,
                 confidence DESC
               LIMIT ?""",
            (max_items,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
