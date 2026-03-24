use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;

#[pyclass]
#[derive(Clone, Debug)]
pub struct ClassificationResult {
    #[pyo3(get)]
    pub route: String,        // "qwen:2b", "qwen:4b", "qwen:9b", "claude"
    #[pyo3(get)]
    pub reason: String,       // why this route was chosen
    #[pyo3(get)]
    pub escalate: bool,       // should escalate to Claude?
    #[pyo3(get)]
    pub needs_search: bool,   // should web search be run first?
    #[pyo3(get)]
    pub confidence: f64,      // 0.0-1.0
    #[pyo3(get)]
    pub domain: String,       // "finance", "sports", "news", "code", "general"
}

#[pymethods]
impl ClassificationResult {
    fn __repr__(&self) -> String {
        format!(
            "ClassificationResult(route='{}', reason='{}', escalate={}, domain='{}')",
            self.route, self.reason, self.escalate, self.domain
        )
    }
}

// Precompiled regex patterns for maximum performance
static ESCALATION_RE: LazyLock<Vec<(Regex, &str)>> = LazyLock::new(|| {
    vec![
        (Regex::new(r"(?i)\b(think|analyze|plan|strategy|advise|architect|design|compare|evaluate|assess|critique|review)\b").unwrap(), "complex_reasoning"),
        (Regex::new(r"(?i)(what do you think|your opinion|what would you|how would you|should i)").unwrap(), "opinion_request"),
        (Regex::new(r"(?i)(portfolio|should i invest|retirement|risk exposure|hedge|diversif)").unwrap(), "financial_advice"),
        (Regex::new(r"(?i)(debug|refactor|architect|code review|write.*function|implement|build.*app)").unwrap(), "code_task"),
        (Regex::new(r"(?i)(wrong|stupid|idiot|terrible|awful|useless|pass to claude|use claude|escalate)").unwrap(), "frustration"),
        (Regex::new(r"(?i)(explain.*why|explain.*how|teach me|help me understand|walk me through)").unwrap(), "deep_explanation"),
    ]
});

static FINANCE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(stock|stocks|share|shares|market|nasdaq|s&p|dow|crypto|bitcoin|btc|eth|ethereum|bond|bonds|yield|treasury|price of|ticker|portfolio|trading|bull|bear|ipo|earnings|dividend|forex)\b").unwrap()
});

static SPORTS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(eagles|phillies|sixers|76ers|flyers|union|nfl|mlb|nba|nhl|mls|score|game|match|season|playoff|draft|roster|standings|touchdown|goal|homerun|three.pointer|hat.trick)\b").unwrap()
});

static NEWS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(news|headline|latest|what happened|whats new|breaking|announce|launch|release|update on|openai|anthropic|google|apple|microsoft|ai news|tech news)\b").unwrap()
});

static TIME_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(what time|what date|what day|current time|today'?s date|right now)\b").unwrap()
});

static SIMPLE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(hi|hello|hey|thanks|thank you|ok|okay|sure|yes|no|good|great|bye|goodbye|good morning|good night|gm|gn)\b").unwrap()
});

/// Classify a user request into a route, domain, and confidence.
/// This is the hot path — called on every single user message.
#[pyfunction]
pub fn classify_request(text: &str) -> ClassificationResult {
    let text = text.trim();
    let word_count = text.split_whitespace().count();
    let question_count = text.matches('?').count();

    // Priority 0: Empty or trivial
    if text.is_empty() || SIMPLE_RE.is_match(text) {
        return ClassificationResult {
            route: "qwen:2b".into(),
            reason: "greeting_or_trivial".into(),
            escalate: false,
            needs_search: false,
            confidence: 0.95,
            domain: "general".into(),
        };
    }

    // Priority 1: Frustration — always escalate
    for (re, reason) in ESCALATION_RE.iter() {
        if *reason == "frustration" && re.is_match(text) {
            return ClassificationResult {
                route: "claude".into(),
                reason: "user_frustration".into(),
                escalate: true,
                needs_search: false,
                confidence: 0.99,
                domain: "general".into(),
            };
        }
    }

    // Priority 2: Explicit escalation triggers
    if word_count > 40 || question_count >= 2 {
        return ClassificationResult {
            route: "claude".into(),
            reason: if word_count > 40 { "long_complex_request" } else { "multi_question" }.into(),
            escalate: true,
            needs_search: false,
            confidence: 0.85,
            domain: detect_domain(text),
        };
    }

    // Priority 3: Keyword-based escalation (complex reasoning, code, etc.)
    for (re, reason) in ESCALATION_RE.iter() {
        if *reason != "frustration" && re.is_match(text) {
            return ClassificationResult {
                route: "claude".into(),
                reason: reason.to_string(),
                escalate: true,
                needs_search: false,
                confidence: 0.80,
                domain: detect_domain(text),
            };
        }
    }

    // Priority 4: Domain-specific routing (check BEFORE time/short queries
    // so "what is the price of bitcoin right now?" hits finance, not time_date)
    if FINANCE_RE.is_match(text) {
        return ClassificationResult {
            route: "qwen:9b".into(),
            reason: "finance_query".into(),
            escalate: false,
            needs_search: true,
            confidence: 0.85,
            domain: "finance".into(),
        };
    }

    if SPORTS_RE.is_match(text) {
        return ClassificationResult {
            route: "qwen:9b".into(),
            reason: "sports_query".into(),
            escalate: false,
            needs_search: true,
            confidence: 0.85,
            domain: "sports".into(),
        };
    }

    if NEWS_RE.is_match(text) {
        return ClassificationResult {
            route: "qwen:9b".into(),
            reason: "news_query".into(),
            escalate: false,
            needs_search: true,
            confidence: 0.80,
            domain: "news".into(),
        };
    }

    // Priority 5: Time/date — fast local (after domain checks so
    // "price of bitcoin right now" doesn't match as time_date)
    if TIME_RE.is_match(text) {
        return ClassificationResult {
            route: "qwen:2b".into(),
            reason: "time_date_query".into(),
            escalate: false,
            needs_search: false,
            confidence: 0.95,
            domain: "general".into(),
        };
    }

    // Priority 6: Short queries — fast model
    if word_count < 5 {
        return ClassificationResult {
            route: "qwen:2b".into(),
            reason: "short_query".into(),
            escalate: false,
            needs_search: false,
            confidence: 0.80,
            domain: detect_domain(text),
        };
    }

    // Priority 7: Medium complexity — general model
    if word_count >= 5 && word_count <= 20 {
        return ClassificationResult {
            route: "qwen:4b".into(),
            reason: "general_factual".into(),
            escalate: false,
            needs_search: false,
            confidence: 0.70,
            domain: detect_domain(text),
        };
    }

    // Default: analysis model
    ClassificationResult {
        route: "qwen:9b".into(),
        reason: "default_analysis".into(),
        escalate: false,
        needs_search: false,
        confidence: 0.60,
        domain: detect_domain(text),
    }
}

fn detect_domain(text: &str) -> String {
    if FINANCE_RE.is_match(text) { "finance".into() }
    else if SPORTS_RE.is_match(text) { "sports".into() }
    else if NEWS_RE.is_match(text) { "news".into() }
    else if Regex::new(r"(?i)\b(code|function|bug|error|compile|deploy|git|api|database|sql)\b").unwrap().is_match(text) { "code".into() }
    else { "general".into() }
}
