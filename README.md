# 🥋 PriceSensei

### *The wisdom to buy right.*

**A multi-agent price monitoring system that searches Google Shopping and Bing Shopping, cleans the noise, and tells you whether to buy now, with three AI agents thinking out loud in a live UI.**

Built solo for the **SerpApi India Hackathon 2026**.

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![SerpApi](https://img.shields.io/badge/data-SerpApi-orange)
![LLM](https://img.shields.io/badge/LLM-Gemini%203.8%20Flash-4285F4)
![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688)
![Streamlit](https://img.shields.io/badge/frontend-Streamlit-FF4B4B)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 📌 Table of Contents

- [The problem](#-the-problem)
- [Our answer](#-our-answer)
- [What it does](#-what-it-does)
- [Tech stack](#-tech-stack)
- [Architecture](#-architecture)
- [Repository structure](#-repository-structure)
- [Setup](#-setup)
- [MCP server](#-mcp-server-claude-desktop)
- [Key technical highlights](#-key-technical-highlights)
- [SerpApi usage](#-serpapi-usage)
- [Testing](#-testing)
- [AI tools disclosure](#-ai-tools-disclosure)
- [What's next](#-whats-next)
- [License](#-license)

---

## 😩 The problem

Searching for a product price online should be easy. It isn't.

- Search "iPhone 15 128GB" and the cheapest results are **₹199 phone cases** and **₹34,999 "Renewed" phones**, not the new phone you asked about.
- The same product is listed under a dozen slightly different titles, and a naive text match merges **128GB with 256GB** or **iPhone 15 with iPhone 15 Plus**.
- One shopping engine is never the whole market.
- Even after you find a number, nobody tells you whether it is actually a *good* number.

## 💡 Our answer

PriceSensei treats price research as a small team of specialists instead of a single search box.

1. **Scout** gathers listings from two engines at once.
2. **Analyst** throws out junk, groups identical products, and scores every price.
3. **Sensei** reads the evidence and gives you a plain-English verdict with a confidence level.

You watch all of it happen live, and the demo never fails: if the LLM is unavailable, a deterministic rule-based verdict takes over.

---

## 🔍 What it does

Enter a product name and an optional budget. Three agents run in sequence and stream their activity to a live web UI.

| Agent | Role | What it does |
|-------|------|--------------|
| 🔍 **Scout** (`SearchAgent`) | Gather | Searches Google Shopping and Bing Shopping via SerpApi, then merges and deduplicates the results |
| 📊 **Analyst** (`AnalysisAgent`) | Clean and score | Filters accessories and renewed products, clusters identical products with fuzzy matching and a numeric-token guard, removes price outliers with Tukey's IQR fences, and flags each cluster `BEST_DEAL`, `FAIR_PRICE` or `OVERPRICED` |
| 🧠 **Sensei** (`VerdictAgent`) | Decide | Calls Gemini 3.8 Flash to write a 2-3 sentence purchase recommendation with a confidence level, reasoning bullets and a suggested action. Falls back to a rule-based verdict if the LLM is unavailable |

### What you see

- A **live event stream** of every agent's activity
- Three **animated agent status cards** (Scout, Analyst, Sensei) with pulsing status dots
- A prominent **"Sensei's Verdict" card** with a confidence-colored gradient
- **Per-cluster price comparison tables** with color-coded deal bands
- A **SerpApi credit meter** in the sidebar

---

## 🛠 Tech stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Data | **SerpApi** `google_shopping` (`gl=in`, `hl=en`) and `bing_shopping` (`mkt=en-IN`). Essential: the app cannot function without it |
| LLM | Google Gemini 3.8 Flash via `google-generativeai` |
| Backend | FastAPI + `sse-starlette` for real-time SSE streaming |
| Frontend | Streamlit with custom HTML/CSS animations |
| Clustering | `thefuzz` (`token_set_ratio`) plus a numeric-token guard |
| Caching | JSON file cache with 24h TTL and visible credit tracking |
| Protection | In-memory rate limiting (5 per IP + 10 global per 30 min) |
| Integration | Model Context Protocol (MCP) server exposing 5 pipeline tools |
| Testing | Custom pytest-free test runners with a network guard |

---

## 🏗 Architecture

```text
User (Streamlit :8501)
    → HTTP/SSE →
FastAPI backend (:8000)
    → Orchestrator (event queue) →
[Scout → SerpApi Google+Bing Shopping]
[Analyst → Relevance filter + Fuzzy clustering + IQR outliers]
[Sensei → Gemini 3.8 Flash]
    → JSON Cache (credit-aware, 24h TTL) + Rate limiter
```

Every agent pushes events onto a shared queue. The orchestrator drains that queue into a Server-Sent Events stream, and the Streamlit UI renders each event the moment it arrives.

---

## 📁 Repository structure

```text
PriceSensei/
├── app.py                              # Streamlit frontend
├── server.py                           # FastAPI SSE backend
├── requirements.txt
├── .env.example
├── README.md
├── LICENSE
├── src/
│   ├── config.py
│   ├── orchestrator.py
│   ├── agents/
│   │   ├── base.py
│   │   ├── search_agent.py             # Scout
│   │   ├── analysis_agent.py           # Analyst
│   │   └── verdict_agent.py            # Sensei
│   ├── models/
│   │   ├── product.py
│   │   ├── analysis.py
│   │   └── verdict.py
│   ├── engines/
│   │   ├── google_shopping.py
│   │   ├── bing_shopping.py
│   │   └── normalizer.py
│   ├── cache/
│   │   └── cache_manager.py
│   └── ui/
│       └── components.py
├── mcp_server/
│   └── server.py                       # 5 MCP tools
└── tests/
    ├── test_day1.py
    ├── test_pipeline.py                # 6/6 passing
    └── test_edge_cases.py              # 8/8 passing
```

---

## 🚀 Setup

### 1. Clone and install

```bash
git clone https://github.com/amanborulkar/PriceSensei.git
cd PriceSensei

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. Add your API keys

```bash
cp .env.example .env             # Windows: copy .env.example .env
```

Open `.env` and fill in:

```env
SERPAPI_KEY=your_serpapi_key
GEMINI_API_KEY=your_gemini_key
```

| Key | Where to get it | Cost |
|-----|-----------------|------|
| `SERPAPI_KEY` | https://serpapi.com/manage-api-key | Free tier: 250 searches/month |
| `GEMINI_API_KEY` | https://aistudio.google.com/app/apikey | Free tier |

### 3. Start the backend

```bash
python server.py
```

The API runs on **http://localhost:8000**.

### 4. Start the frontend

In a **second terminal** (with the venv activated):

```bash
streamlit run app.py
```

The UI runs on **http://localhost:8501**.

### 5. Ask Sensei

Open **http://localhost:8501**, type a product such as `iPhone 15 128GB`, optionally set a budget, and press **🔍 Ask Sensei**.

> **No Gemini key?** The pipeline still completes. Sensei switches to its rule-based verdict and the UI shows which model produced the answer.

---

## 🔌 MCP server (Claude Desktop)

PriceSensei ships a Model Context Protocol server, so any MCP client can run the same pipeline as a set of tools.

Add this to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "pricesensei": {
      "command": "python",
      "args": ["/absolute/path/to/PriceSensei/mcp_server/server.py"],
      "env": {
        "SERPAPI_KEY": "your_serpapi_key",
        "GEMINI_API_KEY": "your_gemini_key"
      }
    }
  }
}
```

Replace `/absolute/path/to/PriceSensei` with the folder you cloned into. If you installed dependencies in a virtual environment, set `"command"` to that environment's Python executable (for example `/absolute/path/to/PriceSensei/.venv/bin/python`). Restart Claude Desktop after saving.

### Available tools

| Tool | Purpose |
|------|---------|
| `search_products` | Run Scout: search both engines and return merged, deduplicated listings |
| `analyze_prices` | Run Analyst: filter, cluster, remove outliers and flag deals |
| `get_verdict` | Run Sensei: get the purchase recommendation |
| `get_credits` | Check SerpApi credit usage and cache statistics |
| `get_pipeline_result` | Fetch the full result of a pipeline run |

---

## ⭐ Key technical highlights

### 🧮 Numeric-token guard on fuzzy clustering

`thefuzz.token_set_ratio` alone would happily merge "iPhone 15 128GB" with "iPhone 15 256GB" or "iPhone 15 Plus", because the strings are almost identical. PriceSensei extracts a **numeric signature** (numbers plus model markers like Pro, Plus, Max, Ultra) from each title and requires signatures to match *before* any fuzzy comparison runs.

```text
"iPhone 15 128GB Black"    -> {15, 128}
"Apple iPhone 15 (128GB)"  -> {15, 128}          merge
"iPhone 15 256GB"          -> {15, 256}          different product
"iPhone 15 Plus 128GB"     -> {15, 128, plus}    different model
```

### 🧹 Relevance filter

Drops accessory listings (case, charger, protector, tempered glass) and condition terms (renewed, refurbished, used, "fair", "good") **unless the query itself contains them**. Searching "renewed iPhone" still works as expected.

Real impact on `iPhone 15 128GB`: **35 of 76 listings (46%)** were filtered out as junk. Without the filter, the verdict pointed at a ₹34,999 renewed phone instead of a new one.

### ⚡ Non-blocking event emission

`BaseAgent.emit` uses `put_nowait` with `QueueFull` handling, so a slow consumer can never stall an agent or freeze the SSE stream.

### 🛡 Zero-IQR guard on outlier detection

If all prices are identical, the interquartile range is zero and the fences collapse onto the data. Outlier removal is skipped in that case rather than deleting legitimate variance.

### 🥋 Rule-based verdict fallback

If Gemini is unreachable or returns unparseable JSON, a deterministic rule-based verdict is produced from the cluster statistics. **The demo never fails.**

### 💾 Aggressive caching

Cache keys are a SHA-256 hash of `(engine, query)` with a 24h TTL, and the credit counter persists across restarts. Repeated demo queries cost **0 credits**.

### 🚦 In-memory rate limiting

5 requests per IP plus 10 global per 30 minutes. This protects the 250 monthly SerpApi credits from accidental or abusive traffic.

### 🔀 Simultaneous dual-engine search

Google Shopping and Bing Shopping are queried via SerpApi and the results are merged and deduplicated by `(title, price)`.

### 🪙 Token discipline

`VerdictAgent` sends only **cluster summaries (max 5)** to Gemini, never raw listings. A typical query uses about **1,000 tokens instead of about 15,000**.

### 🎨 Designed UI

Dark gradient hero header, animated agent status cards with pulsing dots, a confidence-colored gradient verdict card, colored cluster bands, and a styled credit meter. It is deliberately not the default Streamlit look.

---

## 🔎 SerpApi usage

SerpApi is the backbone of PriceSensei, and nothing works without it.

- **`google_shopping`** engine with `gl=in`, `hl=en`
- **`bing_shopping`** engine with `mkt=en-IN`
- Both are called for every query, and results are normalized into one unified `Product` schema
- Credit consumption is tracked per call and visible in the UI
- The 250 monthly free credits are preserved through caching
- A typical uncached query costs about **2 credits**. A cached query costs **0**

---

## 🧪 Testing

```bash
python -m tests.test_pipeline      # 6/6 passing
python -m tests.test_edge_cases    # 8/8 passing
```

Both suites run **offline with no API keys**. A network guard blocks accidental SerpApi and Gemini calls, so running the tests never spends credits.

---

## 🤖 AI tools disclosure

In the interest of transparency:

- **Claude (Anthropic)** was used as a development accelerator for code generation, architecture decisions and iterative review throughout the build.
- **Gemini 3.8 Flash** is the runtime LLM behind the Sensei verdict agent.
- All code was reviewed, integrated, tested and debugged by the developer.

---

## 🔮 What's next

- 📈 **Price history tracking** to show whether today's price is low relative to the last 30 or 90 days
- 💬 **WhatsApp alerts** when a tracked product drops below your target
- 🧩 **Browser extension** that shows the Sensei verdict on any product page
- 📷 **Barcode scan** to check a price in-store from your phone
- 🛒 **More engines** to widen market coverage beyond Google and Bing

---

## 📄 License

Released under the [MIT License](LICENSE).

---

<p align="center">
  Built solo for the SerpApi India Hackathon 2026 by <a href="https://github.com/amanborulkar">@amanborulkar</a><br>
  🥋 <em>The wisdom to buy right.</em>
</p>