"""Central configuration. Override anything with environment variables."""
import os
import pathlib

BASE = pathlib.Path(__file__).parent
DATA = BASE / "data"
DB_PATH = pathlib.Path(os.getenv("JIRANI_DB", BASE / "jirani.db"))

# Bump this when you change rules, prompts or validation. Log it in the evaluation record,
# and tag the git commit with it BEFORE the trainer releases the six final tickets.
APP_VERSION = "0.1.0"

# Model names change often. Check https://ai.google.dev/gemini-api/docs/models and override with JIRANI_MODEL if needed.
MODEL = os.getenv("JIRANI_MODEL", "gemini-3.8-flash")
# Tried in order if the main model is overloaded (503/429), timing out, or not found (404).
# Authentication errors are NOT retried on another model. The model that really wrote each draft is recorded.
FALLBACK_MODELS = [m.strip() for m in os.getenv("JIRANI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-2.5-flash").split(",") if m.strip()]
# auto = use Gemini if GEMINI_API_KEY (or GOOGLE_API_KEY) is set, otherwise offline templates (clearly labelled)
DRAFT_MODE = os.getenv("JIRANI_DRAFT_MODE", "auto")  # auto | llm | template

N8N_WEBHOOK_URL = os.getenv("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/jirani-review-outcome")
N8N_TIMEOUT_S = float(os.getenv("N8N_TIMEOUT_S", "10"))
