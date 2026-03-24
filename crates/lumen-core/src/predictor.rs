use pyo3::prelude::*;
use std::collections::HashMap;

#[pyclass]
#[derive(Clone, Debug)]
pub struct PredictionResult {
    #[pyo3(get)]
    pub prediction: String,      // what we think the user wants
    #[pyo3(get)]
    pub confidence: f64,         // 0.0-1.0
    #[pyo3(get)]
    pub basis: String,           // why we think this
    #[pyo3(get)]
    pub action: String,          // suggested proactive action
    #[pyo3(get)]
    pub priority: u8,            // 1-5 (1=highest)
}

#[pymethods]
impl PredictionResult {
    fn __repr__(&self) -> String {
        format!(
            "PredictionResult(prediction='{}', confidence={:.2}, priority={})",
            self.prediction, self.confidence, self.priority
        )
    }
}

/// Predict what the user might want based on time, recent topics, mood, and patterns.
///
/// Parameters:
/// - hour: Current hour (0-23)
/// - day_of_week: 0=Monday, 6=Sunday
/// - recent_topics: JSON map of topic → count from recent conversations
/// - mood_compound: Recent mood score (-1 to 1)
/// - last_query_domain: Domain of the user's last query
/// - minutes_since_last: Minutes since user's last interaction
/// - game_today: Whether a Philadelphia team plays today
#[pyfunction]
#[pyo3(signature = (hour, day_of_week, recent_topics, mood_compound, last_query_domain="", minutes_since_last=0, game_today=false))]
pub fn predict_intent(
    hour: u8,
    day_of_week: u8,
    recent_topics: HashMap<String, u32>,
    mood_compound: f64,
    last_query_domain: &str,
    minutes_since_last: u32,
    game_today: bool,
) -> Vec<PredictionResult> {
    let mut predictions: Vec<PredictionResult> = Vec::new();
    let is_weekday = day_of_week < 5;
    let is_market_hours = is_weekday && hour >= 9 && hour <= 16;

    // Morning briefing (6-9am weekdays)
    if hour >= 6 && hour <= 9 && is_weekday && minutes_since_last > 60 {
        predictions.push(PredictionResult {
            prediction: "Morning briefing".into(),
            confidence: 0.85,
            basis: "weekday_morning_routine".into(),
            action: "generate_morning_brief".into(),
            priority: 1,
        });
    }

    // Market open check (9:30am weekdays)
    if hour == 9 && is_weekday {
        let finance_interest = recent_topics.get("finance").copied().unwrap_or(0);
        if finance_interest > 0 {
            predictions.push(PredictionResult {
                prediction: "Market opening summary".into(),
                confidence: 0.80,
                basis: format!("market_open+{}x_finance_interest", finance_interest),
                action: "run_finance_brief".into(),
                priority: 1,
            });
        }
    }

    // During market hours — proactive alerts
    if is_market_hours {
        predictions.push(PredictionResult {
            prediction: "Market movement alerts".into(),
            confidence: 0.60,
            basis: "market_hours_active".into(),
            action: "check_watchlist_alerts".into(),
            priority: 3,
        });
    }

    // Game day predictions
    if game_today {
        if hour >= 16 && hour <= 19 {
            predictions.push(PredictionResult {
                prediction: "Pre-game update".into(),
                confidence: 0.80,
                basis: "game_today_evening".into(),
                action: "fetch_pregame_info".into(),
                priority: 2,
            });
        }
        if hour >= 20 {
            predictions.push(PredictionResult {
                prediction: "Live score check".into(),
                confidence: 0.75,
                basis: "game_likely_in_progress".into(),
                action: "fetch_live_scores".into(),
                priority: 1,
            });
        }
    }

    // Evening news digest
    if hour >= 18 && hour <= 20 && minutes_since_last > 120 {
        let news_interest = recent_topics.get("news").copied().unwrap_or(0);
        if news_interest > 0 || recent_topics.get("tech").copied().unwrap_or(0) > 0 {
            predictions.push(PredictionResult {
                prediction: "Evening tech/AI news digest".into(),
                confidence: 0.70,
                basis: format!("evening_routine+{}x_news_interest", news_interest),
                action: "generate_news_digest".into(),
                priority: 2,
            });
        }
    }

    // Mood-based predictions
    if mood_compound < -0.3 {
        predictions.push(PredictionResult {
            prediction: "Adjust tone — user may be frustrated".into(),
            confidence: (mood_compound.abs() * 1.2).min(0.95),
            basis: format!("negative_mood:{:.2}", mood_compound),
            action: "set_tone_direct".into(),
            priority: 1,
        });
    }

    // Topic momentum — if user keeps asking about something, predict more
    for (topic, &count) in &recent_topics {
        if count >= 3 {
            let conf = (0.5 + (count as f64 * 0.1)).min(0.90);
            predictions.push(PredictionResult {
                prediction: format!("User focused on {} — surface related content", topic),
                confidence: conf,
                basis: format!("topic_momentum:{}x{}", topic, count),
                action: format!("proactive_{}_update", topic),
                priority: 2,
            });
        }
    }

    // Re-engagement after long absence
    if minutes_since_last > 240 {
        predictions.push(PredictionResult {
            prediction: "Welcome back summary".into(),
            confidence: 0.65,
            basis: format!("{}min_absence", minutes_since_last),
            action: "generate_catchup_brief".into(),
            priority: 2,
        });
    }

    // Weekend leisure mode
    if !is_weekday && hour >= 10 && hour <= 14 {
        let sports_interest = recent_topics.get("sports").copied().unwrap_or(0);
        if sports_interest > 0 || game_today {
            predictions.push(PredictionResult {
                prediction: "Weekend sports roundup".into(),
                confidence: 0.65,
                basis: "weekend_sports_interest".into(),
                action: "generate_sports_roundup".into(),
                priority: 3,
            });
        }
    }

    // Sort by priority (ascending) then confidence (descending)
    predictions.sort_by(|a, b| {
        a.priority.cmp(&b.priority)
            .then(b.confidence.partial_cmp(&a.confidence).unwrap_or(std::cmp::Ordering::Equal))
    });

    predictions
}
