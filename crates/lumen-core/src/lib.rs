use pyo3::prelude::*;

mod classifier;
mod guardrails;
mod sentiment;
mod predictor;
mod tts_prep;

/// Lumen Core — high-performance engine for the Lumen AI assistant.
/// Exposes Rust functions to Python via PyO3.
#[pymodule]
fn lumen_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(classifier::classify_request, m)?)?;
    m.add_function(wrap_pyfunction!(sentiment::analyze_sentiment, m)?)?;
    m.add_function(wrap_pyfunction!(guardrails::check_output, m)?)?;
    m.add_function(wrap_pyfunction!(predictor::predict_intent, m)?)?;
    m.add_class::<classifier::ClassificationResult>()?;
    m.add_class::<sentiment::SentimentResult>()?;
    m.add_class::<guardrails::GuardrailResult>()?;
    m.add_class::<predictor::PredictionResult>()?;
    m.add_function(wrap_pyfunction!(tts_prep::prepare_for_tts, m)?)?;
    m.add_class::<tts_prep::TTSPrepResult>()?;
    Ok(())
}
