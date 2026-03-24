use pyo3::prelude::*;
use regex::Regex;
use std::collections::HashMap;
use std::sync::LazyLock;

#[pyclass]
#[derive(Clone, Debug)]
pub struct TTSPrepResult {
    #[pyo3(get)]
    pub text: String,
    #[pyo3(get)]
    pub sentences: Vec<String>,
    #[pyo3(get)]
    pub estimated_duration_ms: u64,
}

#[pymethods]
impl TTSPrepResult {
    fn __repr__(&self) -> String {
        format!(
            "TTSPrepResult(text='{}...', sentences={}, duration_ms={})",
            &self.text.chars().take(60).collect::<String>(),
            self.sentences.len(),
            self.estimated_duration_ms
        )
    }
}

// --- Precompiled regex patterns via LazyLock ---

static RE_CODE_BLOCK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?s)```.*?```").unwrap()
});

static RE_INLINE_CODE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"`[^`]+`").unwrap()
});

static RE_BOLD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\*\*(.+?)\*\*").unwrap()
});

static RE_ITALIC: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\*(.+?)\*").unwrap()
});

static RE_HEADERS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?m)^#{1,6}\s*").unwrap()
});

static RE_STANDALONE_ASTERISKS: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\*+").unwrap()
});

static RE_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"https?://\S+").unwrap()
});

static RE_EMOJI: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[\x{1F000}-\x{1FFFF}\x{2000}-\x{27BF}]").unwrap()
});

static RE_TICKER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(BTC|ETH|AAPL|MSFT|GOOGL|AMZN|TSLA|NVDA|META|SOL|XRP|DOGE|SPY|QQQ|BNB|ADA)\b").unwrap()
});

// Match optional preceding "up"/"down" + signed percent to avoid "up up 3 percent"
static RE_SIGNED_PERCENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(?:up\s+|down\s+)?([+-])(\d+(?:\.\d+)?)%").unwrap()
});

static RE_UNSIGNED_PERCENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(\d+(?:\.\d+)?)%").unwrap()
});

static RE_CURRENCY: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\$(\d{1,3}(?:,\d{3})*)(?:\.(\d{2}))?").unwrap()
});

static RE_NUMBER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\b").unwrap()
});

static RE_ST: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\bSt\.\s+([A-Z])").unwrap()
});

static RE_NON_ALNUM: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[^a-zA-Z0-9\s']").unwrap()
});

static RE_MULTI_SPACE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\s{2,}").unwrap()
});

static RE_SENTENCE_SPLIT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[.!?]+").unwrap()
});

// --- Static ticker map ---

static TICKER_MAP: LazyLock<HashMap<&str, &str>> = LazyLock::new(|| {
    HashMap::from([
        ("BTC", "Bitcoin"),
        ("ETH", "Ethereum"),
        ("AAPL", "Apple"),
        ("MSFT", "Microsoft"),
        ("GOOGL", "Google"),
        ("AMZN", "Amazon"),
        ("TSLA", "Tesla"),
        ("NVDA", "Nvidia"),
        ("META", "Meta"),
        ("SOL", "Solana"),
        ("XRP", "Ripple"),
        ("DOGE", "Dogecoin"),
        ("SPY", "S&P 500 ETF"),
        ("QQQ", "Nasdaq ETF"),
        ("BNB", "Binance Coin"),
        ("ADA", "Cardano"),
    ])
});

// --- Number to words ---

const ONES: [&str; 20] = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen",
];

const TENS: [&str; 10] = [
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
];

fn below_thousand(n: u64) -> String {
    if n == 0 {
        return String::new();
    }
    if n < 20 {
        return ONES[n as usize].to_string();
    }
    if n < 100 {
        let t = TENS[(n / 10) as usize];
        let o = n % 10;
        if o > 0 {
            return format!("{} {}", t, ONES[o as usize]);
        }
        return t.to_string();
    }
    let h = ONES[(n / 100) as usize];
    let rem = n % 100;
    if rem == 0 {
        format!("{} hundred", h)
    } else {
        format!("{} hundred {}", h, below_thousand(rem))
    }
}

fn number_to_words(n: u64) -> String {
    if n == 0 {
        return "zero".to_string();
    }
    if n < 1000 {
        return below_thousand(n);
    }
    // 1000-999999
    let thousands = n / 1000;
    let remainder = n % 1000;
    let t_part = format!("{} thousand", below_thousand(thousands));
    if remainder == 0 {
        t_part
    } else {
        format!("{} {}", t_part, below_thousand(remainder))
    }
}

fn digit_word(d: u8) -> &'static str {
    match d {
        0 => "zero", 1 => "one", 2 => "two", 3 => "three", 4 => "four",
        5 => "five", 6 => "six", 7 => "seven", 8 => "eight", 9 => "nine",
        _ => "",
    }
}

/// Convert a numeric string (possibly with commas and decimals) to words.
/// "3.14" -> "three point one four"
/// "1,234" -> "one thousand two hundred thirty four"
fn decimal_to_words(s: &str) -> String {
    let clean = s.replace(',', "");
    let negative = clean.starts_with('-');
    let clean = clean.trim_start_matches('-');

    let parts: Vec<&str> = clean.split('.').collect();
    let int_val: u64 = parts[0].parse().unwrap_or(0);
    let mut result = if negative {
        format!("negative {}", number_to_words(int_val))
    } else {
        number_to_words(int_val)
    };

    if parts.len() == 2 && !parts[1].is_empty() {
        result.push_str(" point");
        for ch in parts[1].chars() {
            if let Some(d) = ch.to_digit(10) {
                result.push(' ');
                result.push_str(digit_word(d as u8));
            }
        }
    }

    result
}

fn cents_to_words(cents_str: &str) -> String {
    let val: u64 = cents_str.parse().unwrap_or(0);
    number_to_words(val)
}

// --- Abbreviation table ---

const ABBREVIATIONS: &[(&str, &str)] = &[
    ("Mr.", "Mister"),
    ("Mrs.", "Missus"),
    ("Dr.", "Doctor"),
    ("vs.", "versus"),
    ("etc.", "et cetera"),
    ("e.g.", "for example"),
    ("i.e.", "that is"),
    ("Ave.", "Avenue"),
    ("Blvd.", "Boulevard"),
];

// --- Main TTS prep function ---

/// Prepare text for TTS: strip markdown, expand symbols/numbers/abbreviations,
/// split into sentences, estimate duration.
/// Replaces the JavaScript preprocessing that was in lumen.js.
#[pyfunction]
pub fn prepare_for_tts(text: &str) -> TTSPrepResult {
    let mut t = text.to_string();

    // Step 1: Strip markdown
    t = RE_CODE_BLOCK.replace_all(&t, "").to_string();
    t = RE_INLINE_CODE.replace_all(&t, "").to_string();
    t = RE_BOLD.replace_all(&t, "$1").to_string();
    t = RE_ITALIC.replace_all(&t, "$1").to_string();
    t = RE_HEADERS.replace_all(&t, "").to_string();
    t = RE_STANDALONE_ASTERISKS.replace_all(&t, "").to_string();

    // Step 2: Strip URLs
    t = RE_URL.replace_all(&t, "").to_string();

    // Step 3: Strip emoji
    t = RE_EMOJI.replace_all(&t, "").to_string();

    // Step 4: Expand ticker symbols
    t = RE_TICKER.replace_all(&t, |caps: &regex::Captures| {
        let ticker = caps.get(1).unwrap().as_str();
        TICKER_MAP.get(ticker).copied().unwrap_or(ticker).to_string()
    }).to_string();

    // Step 5: Convert percentages (signed first, then unsigned)
    t = RE_SIGNED_PERCENT.replace_all(&t, |caps: &regex::Captures| {
        let sign = caps.get(1).unwrap().as_str();
        let num = caps.get(2).unwrap().as_str();
        let word_num = decimal_to_words(num);
        let direction = if sign == "+" { "up" } else { "down" };
        format!("{} {} percent", direction, word_num)
    }).to_string();

    t = RE_UNSIGNED_PERCENT.replace_all(&t, |caps: &regex::Captures| {
        let num = caps.get(1).unwrap().as_str();
        format!("{} percent", decimal_to_words(num))
    }).to_string();

    // Step 6: Convert currency
    t = RE_CURRENCY.replace_all(&t, |caps: &regex::Captures| {
        let whole_str = caps.get(1).unwrap().as_str().replace(',', "");
        let whole: u64 = whole_str.parse().unwrap_or(0);
        let whole_words = number_to_words(whole);
        if let Some(cents_match) = caps.get(2) {
            let cents_words = cents_to_words(cents_match.as_str());
            format!("{} dollars and {} cents", whole_words, cents_words)
        } else {
            format!("{} dollars", whole_words)
        }
    }).to_string();

    // Step 7: Convert remaining numbers to words
    t = RE_NUMBER.replace_all(&t, |caps: &regex::Captures| {
        decimal_to_words(caps.get(1).unwrap().as_str())
    }).to_string();

    // Step 8: Expand abbreviations
    t = RE_ST.replace_all(&t, "Street $1").to_string();
    for (abbr, expansion) in ABBREVIATIONS {
        t = t.replace(abbr, expansion);
    }

    // Step 11: Split into sentences BEFORE stripping punctuation (needs . ! ?)
    let raw_sentences: Vec<&str> = RE_SENTENCE_SPLIT.split(&t).collect();

    // Step 9: Strip remaining non-alphanumeric except spaces and apostrophes
    // Step 10: Normalize whitespace
    // Apply to each sentence individually
    let sentences: Vec<String> = raw_sentences
        .iter()
        .map(|s| {
            let cleaned = RE_NON_ALNUM.replace_all(s, " ");
            let normalized = RE_MULTI_SPACE.replace_all(&cleaned, " ");
            normalized.trim().to_string()
        })
        .filter(|s| s.len() >= 3)
        .collect();

    // Also produce the full cleaned text
    let full_text = sentences.join(". ");

    // Step 12: Estimate duration — 150 WPM = 400ms per word, min 800ms per sentence
    let total_words = full_text.split_whitespace().count() as u64;
    let word_duration = total_words * 400;
    let sentence_minimum = sentences.len() as u64 * 800;
    let estimated_duration_ms = word_duration.max(sentence_minimum);

    TTSPrepResult {
        text: full_text,
        sentences,
        estimated_duration_ms,
    }
}
