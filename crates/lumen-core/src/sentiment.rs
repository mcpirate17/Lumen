use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::LazyLock;

#[pyclass]
#[derive(Clone, Debug)]
pub struct SentimentResult {
    #[pyo3(get)]
    pub compound: f64,    // -1.0 to 1.0 (overall sentiment)
    #[pyo3(get)]
    pub positive: f64,    // 0.0 to 1.0
    #[pyo3(get)]
    pub negative: f64,    // 0.0 to 1.0
    #[pyo3(get)]
    pub neutral: f64,     // 0.0 to 1.0
    #[pyo3(get)]
    pub mood: String,     // "positive", "negative", "neutral", "very_positive", "very_negative"
}

#[pymethods]
impl SentimentResult {
    fn __repr__(&self) -> String {
        format!(
            "SentimentResult(compound={:.3}, mood='{}')",
            self.compound, self.mood
        )
    }
}

// VADER lexicon — core sentiment words with their valence scores.
// This is a subset of the full VADER lexicon optimized for conversational AI.
// Scores range from -4.0 (most negative) to +4.0 (most positive).
static LEXICON: LazyLock<HashMap<&str, f64>> = LazyLock::new(|| {
    let mut m = HashMap::with_capacity(400);
    // Positive words
    for (w, s) in [
        ("good", 1.9), ("great", 3.1), ("awesome", 3.4), ("amazing", 3.3),
        ("excellent", 3.2), ("fantastic", 3.4), ("wonderful", 3.3), ("love", 3.2),
        ("like", 1.5), ("enjoy", 2.0), ("happy", 2.7), ("glad", 2.1),
        ("perfect", 3.0), ("best", 3.2), ("beautiful", 3.0), ("brilliant", 3.2),
        ("cool", 1.9), ("nice", 1.8), ("fun", 2.4), ("interesting", 1.9),
        ("helpful", 2.1), ("useful", 1.8), ("thanks", 1.5), ("thank", 1.5),
        ("pleased", 2.2), ("impressive", 2.8), ("outstanding", 3.3),
        ("superb", 3.1), ("terrific", 3.0), ("delightful", 3.0),
        ("excited", 2.8), ("thrilled", 3.1), ("fortunate", 2.2),
        ("lucky", 2.1), ("blessed", 2.4), ("grateful", 2.5),
        ("appreciate", 2.3), ("win", 2.4), ("won", 2.4), ("success", 2.6),
        ("successful", 2.6), ("triumph", 3.0), ("victory", 3.0),
        ("profit", 1.9), ("gain", 1.8), ("bull", 1.2), ("bullish", 1.8),
        ("rally", 1.5), ("surge", 1.6), ("boom", 1.8), ("growth", 1.5),
        ("up", 0.5), ("rising", 1.0), ("higher", 0.8), ("positive", 1.5),
        ("strong", 1.5), ("solid", 1.3), ("robust", 1.5), ("healthy", 1.5),
        ("right", 0.8), ("correct", 1.2), ("yes", 0.6), ("agree", 1.3),
        ("absolutely", 1.8), ("definitely", 1.5), ("exactly", 1.5),
    ] {
        m.insert(w, s);
    }
    // Negative words
    for (w, s) in [
        ("bad", -2.5), ("terrible", -3.4), ("awful", -3.2), ("horrible", -3.3),
        ("hate", -3.4), ("dislike", -2.0), ("angry", -2.7), ("sad", -2.1),
        ("unhappy", -2.2), ("disappointing", -2.5), ("disappointed", -2.5),
        ("worst", -3.4), ("ugly", -2.5), ("stupid", -2.8), ("dumb", -2.6),
        ("boring", -2.0), ("useless", -2.8), ("wrong", -2.0), ("fail", -2.5),
        ("failed", -2.5), ("failure", -2.8), ("broken", -2.2), ("bug", -1.5),
        ("error", -1.8), ("crash", -2.5), ("problem", -1.8), ("issue", -1.5),
        ("sucks", -2.8), ("crap", -2.6), ("damn", -1.8), ("hell", -1.5),
        ("annoying", -2.2), ("frustrating", -2.5), ("frustrated", -2.5),
        ("confused", -1.8), ("confusing", -2.0), ("painful", -2.4),
        ("worried", -1.8), ("anxious", -2.0), ("afraid", -2.1), ("fear", -2.0),
        ("scary", -2.2), ("nervous", -1.8), ("stressed", -2.2), ("stress", -2.0),
        ("loss", -2.2), ("lost", -1.8), ("lose", -2.0), ("bear", -0.8),
        ("bearish", -1.8), ("crash", -2.5), ("dump", -2.0), ("plunge", -2.5),
        ("decline", -1.8), ("drop", -1.5), ("falling", -1.5), ("down", -0.8),
        ("lower", -0.8), ("negative", -1.5), ("weak", -1.5), ("poor", -2.0),
        ("miss", -1.5), ("missed", -1.5), ("missing", -1.2), ("lack", -1.5),
        ("no", -0.5), ("not", -0.5), ("never", -1.2), ("nothing", -1.0),
        ("nobody", -1.0), ("nowhere", -1.0), ("neither", -0.8),
        ("kill", -3.0), ("die", -2.5), ("dead", -2.5), ("death", -2.8),
        ("sick", -1.8), ("ill", -1.5), ("pain", -2.2), ("hurt", -2.2),
        ("suffer", -2.5), ("suffer", -2.5), ("miserable", -3.0),
    ] {
        m.insert(w, s);
    }
    // Intensifiers (boosters)
    for (w, s) in [
        ("very", 0.0), ("really", 0.0), ("extremely", 0.0),
        ("incredibly", 0.0), ("absolutely", 0.0), ("totally", 0.0),
        ("super", 0.0), ("so", 0.0), ("quite", 0.0),
    ] {
        m.insert(w, s);
    }
    m
});

static BOOSTERS: LazyLock<HashMap<&str, f64>> = LazyLock::new(|| {
    let mut m = HashMap::new();
    for (w, s) in [
        ("very", 0.293), ("really", 0.293), ("extremely", 0.293),
        ("incredibly", 0.293), ("absolutely", 0.293), ("totally", 0.293),
        ("super", 0.293), ("so", 0.293), ("quite", 0.146),
        ("rather", 0.146), ("somewhat", -0.146), ("kind of", -0.146),
        ("sort of", -0.146), ("barely", -0.293), ("hardly", -0.293),
    ] {
        m.insert(w, s);
    }
    m
});

static NEGATIONS: LazyLock<Vec<&str>> = LazyLock::new(|| {
    vec![
        "not", "isn't", "isnt", "wasn't", "wasnt", "aren't", "arent",
        "weren't", "werent", "won't", "wont", "wouldn't", "wouldnt",
        "shouldn't", "shouldnt", "couldn't", "couldnt", "don't", "dont",
        "doesn't", "doesnt", "didn't", "didnt", "haven't", "havent",
        "hasn't", "hasnt", "hadn't", "hadnt", "never", "no", "none",
        "neither", "nor", "nothing", "nowhere", "nobody",
    ]
});

/// Analyze sentiment of text using VADER-inspired algorithm.
/// Returns compound score (-1 to 1), positive/negative/neutral ratios, and mood label.
/// Runs in <1ms for typical messages — designed for per-message analysis.
#[pyfunction]
pub fn analyze_sentiment(text: &str) -> SentimentResult {
    let text_lower = text.to_lowercase();
    let words: Vec<&str> = text_lower.split_whitespace().collect();
    let mut sentiments: Vec<f64> = Vec::new();

    for (i, word) in words.iter().enumerate() {
        // Strip punctuation for lookup
        let clean: String = word.chars().filter(|c| c.is_alphanumeric()).collect();
        if clean.is_empty() {
            continue;
        }

        if let Some(&valence) = LEXICON.get(clean.as_str()) {
            if valence == 0.0 {
                continue; // Skip boosters in direct lookup
            }
            let mut v = valence;

            // Check for negation in previous 3 words
            let start = if i >= 3 { i - 3 } else { 0 };
            for j in start..i {
                if NEGATIONS.contains(&words[j]) {
                    v *= -0.74; // VADER negation constant
                    break;
                }
            }

            // Check for booster in previous word
            if i > 0 {
                if let Some(&boost) = BOOSTERS.get(words[i - 1]) {
                    if v > 0.0 {
                        v += boost;
                    } else {
                        v -= boost;
                    }
                }
            }

            // ALL CAPS amplification
            if word.chars().all(|c| c.is_uppercase()) && word.len() > 1 {
                if v > 0.0 { v += 0.733; } else { v -= 0.733; }
            }

            sentiments.push(v);
        }
    }

    // Exclamation point amplification
    let excl_count = text.matches('!').count().min(4);
    let excl_amp = excl_count as f64 * 0.292;

    // Question mark dampening (for negative sentiment)
    let quest_count = text.matches('?').count();
    let quest_damp = if quest_count > 1 { 0.96 } else { 1.0 };

    if sentiments.is_empty() {
        return SentimentResult {
            compound: 0.0,
            positive: 0.0,
            negative: 0.0,
            neutral: 1.0,
            mood: "neutral".into(),
        };
    }

    // Sum and normalize
    let mut sum: f64 = sentiments.iter().sum();

    // Apply punctuation modifiers
    if sum > 0.0 {
        sum += excl_amp;
    } else if sum < 0.0 {
        sum -= excl_amp;
    }
    sum *= quest_damp;

    // Normalize to -1..1 using VADER's normalization
    let compound = normalize(sum);

    // Calculate positive/negative/neutral proportions
    let mut pos_sum: f64 = 0.0;
    let mut neg_sum: f64 = 0.0;
    let mut neu_count: f64 = 0.0;

    for &s in &sentiments {
        if s > 0.05 { pos_sum += s + 1.0; }
        else if s < -0.05 { neg_sum += s - 1.0; }
        else { neu_count += 1.0; }
    }

    let total = pos_sum + neg_sum.abs() + neu_count;
    let positive = if total > 0.0 { (pos_sum / total * 100.0).round() / 100.0 } else { 0.0 };
    let negative = if total > 0.0 { (neg_sum.abs() / total * 100.0).round() / 100.0 } else { 0.0 };
    let neutral = if total > 0.0 { (neu_count / total * 100.0).round() / 100.0 } else { 1.0 };

    let mood = match compound {
        c if c >= 0.5 => "very_positive",
        c if c >= 0.05 => "positive",
        c if c <= -0.5 => "very_negative",
        c if c <= -0.05 => "negative",
        _ => "neutral",
    };

    SentimentResult {
        compound,
        positive,
        negative,
        neutral,
        mood: mood.into(),
    }
}

/// VADER normalization: squish score into -1..1
fn normalize(score: f64) -> f64 {
    let alpha = 15.0; // VADER's alpha constant
    score / (score * score + alpha).sqrt()
}
