"""Central configuration for PriceSensei."""
import os
from dotenv import load_dotenv

load_dotenv()

# --- Branding ---
APP_NAME = "PriceSensei"
APP_TAGLINE = "The wisdom to buy right."
APP_EMOJI = "🥋"

# --- SerpApi ---
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
MONTHLY_CREDIT_LIMIT = 250
CREDIT_WARNING_THRESHOLD = 200

# --- LLM (Verdict Agent / Sensei) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
LLM_MODEL = "gemini-3.8-flash"

# --- Cache ---
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(_THIS_DIR, "cache", "cache_store")
CACHE_TTL_HOURS = 24

# --- Search ---
MAX_PAGES_PER_ENGINE = 1
RESULTS_PER_PAGE = 60

# --- Engines ---
ENGINES = [
    {"name": "google_shopping", "label": "Google Shopping"},
    {"name": "bing_shopping", "label": "Bing Shopping"},
]

# --- Agent friendly labels (used by SSE UI) ---
AGENT_LABELS = {
    "search_agent": "Scout",
    "analysis_agent": "Analyst",
    "verdict_agent": "Sensei",
}