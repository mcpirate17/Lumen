use pyo3::prelude::*;
use regex::Regex;
use std::sync::LazyLock;

#[pyclass]
#[derive(Clone, Debug)]
pub struct GuardrailResult {
    #[pyo3(get)]
    pub safe: bool,
    #[pyo3(get)]
    pub reason: String,         // "pass", "blocked_pattern", "quality_fail", "repetition", etc.
    #[pyo3(get)]
    pub quality_score: f64,     // 0.0-1.0
    #[pyo3(get)]
    pub needs_disclaimer: bool, // financial advice needs disclaimer
}

#[pymethods]
impl GuardrailResult {
    fn __repr__(&self) -> String {
        format!(
            "GuardrailResult(safe={}, reason='{}', quality={:.2})",
            self.safe, self.reason, self.quality_score
        )
    }
}

// Blocked content patterns — things a personal assistant should never say.
// NOTE: These only block HARD violations. Lumen's personality allows honest "I don't know"
// responses, so ai_self_reference and helplessness patterns are removed.
// Those patterns were blocking legitimate, helpful responses where the model
// acknowledged limitations and suggested alternatives (which is exactly what we want).
static BLOCKED_PATTERNS: LazyLock<Vec<(Regex, &str)>> = LazyLock::new(|| {
    vec![
        (Regex::new(r"(?i)(kill|harm|hurt|injure)\s+(yourself|himself|herself|themselves|myself)").unwrap(), "self_harm"),
        (Regex::new(r"(?i)i\s+(hate|despise|loathe)\s+you").unwrap(), "hostile_to_user"),
        (Regex::new(r"(?i)(guarantee|guaranteed|certain)\s+(return|profit|gain|money)").unwrap(), "financial_guarantee"),
        (Regex::new(r"(?i)you\s+(should|must|need to)\s+(buy|sell|invest|short)\b").unwrap(), "unqualified_advice"),
    ]
});

// Finance disclaimer triggers
static FINANCE_ADVICE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)(should (buy|sell|hold|invest)|recommend|pick|best stock|must buy|must sell|buy signal|sell signal)").unwrap()
});

/// Detect repetition loops without backreferences.
/// Checks for any word repeated 3+ times consecutively.
fn has_repetition(text: &str) -> bool {
    let words: Vec<&str> = text.split_whitespace().collect();
    if words.len() < 3 {
        return false;
    }
    let mut repeat_count = 1u32;
    for i in 1..words.len() {
        if words[i].eq_ignore_ascii_case(words[i - 1]) {
            repeat_count += 1;
            if repeat_count >= 3 {
                return true;
            }
        } else {
            repeat_count = 1;
        }
    }
    false
}

/// Check an LLM output for safety, quality, and compliance.
/// Called on every Qwen response before delivery to user.
/// Returns pass/fail with reason and quality score.
///
/// Parameters:
/// - output: The LLM's response text
/// - domain: The domain context ("finance", "sports", "general", etc.)
/// - min_words: Minimum acceptable word count
/// - min_chars: Minimum acceptable character count
#[pyfunction]
#[pyo3(signature = (output, domain="general", min_words=4, min_chars=20))]
pub fn check_output(output: &str, domain: &str, min_words: usize, min_chars: usize) -> GuardrailResult {
    let output = output.trim();

    // Check 1: Empty or near-empty
    if output.is_empty() {
        return GuardrailResult {
            safe: false,
            reason: "empty_response".into(),
            quality_score: 0.0,
            needs_disclaimer: false,
        };
    }

    // Check 2: Blocked patterns
    for (re, reason) in BLOCKED_PATTERNS.iter() {
        if re.is_match(output) {
            return GuardrailResult {
                safe: false,
                reason: format!("blocked:{}", reason),
                quality_score: 0.0,
                needs_disclaimer: false,
            };
        }
    }

    // Check 3: Repetition loops
    if has_repetition(output) {
        return GuardrailResult {
            safe: false,
            reason: "repetition_loop".into(),
            quality_score: 0.1,
            needs_disclaimer: false,
        };
    }

    // Check 4: Minimum length
    let word_count = output.split_whitespace().count();
    let char_count = output.len();

    if word_count < min_words || char_count < min_chars {
        return GuardrailResult {
            safe: false,
            reason: format!("too_short:{}w_{}c", word_count, char_count),
            quality_score: 0.2,
            needs_disclaimer: false,
        };
    }

    // Check 5: Ends with punctuation (quality signal)
    let ends_ok = output.ends_with('.') || output.ends_with('!')
        || output.ends_with('?') || output.ends_with('"')
        || output.ends_with(')') || output.ends_with(':')
        || output.ends_with('-') || output.ends_with('*');

    // Check 6: Finance disclaimer needed?
    let needs_disclaimer = domain == "finance" && FINANCE_ADVICE_RE.is_match(output);

    // Calculate quality score
    let mut quality = 1.0_f64;
    if !ends_ok { quality -= 0.15; }
    if word_count < 8 { quality -= 0.1; }

    // Penalize excessive length (Qwen rambling)
    if word_count > 300 { quality -= 0.2; }

    // Penalize if contains common Qwen artifacts
    if output.contains("<think>") || output.contains("</think>")
        || output.contains("<|") || output.contains("|>") {
        quality -= 0.3;
    }

    quality = quality.clamp(0.0, 1.0);

    GuardrailResult {
        safe: true,
        reason: "pass".into(),
        quality_score: quality,
        needs_disclaimer,
    }
}
