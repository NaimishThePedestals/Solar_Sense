# ☀ SolarSense AI

A multi-agent CLI tool that analyzes solar panel efficiency for any location on Earth and gives you ranked, actionable recommendations.

Built with **LangGraph + DeepSeek V4** and **free public data APIs** (PVGIS, NASA POWER, Open-Meteo).

## Architecture

```
   ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
   │   COLLECT    │ ──> │   ANALYZE    │ ──> │    ADVISE    │
   │   (4 APIs)   │     │  (DeepSeek)  │     │  (DeepSeek)  │
   └──────────────┘     └──────────────┘     └──────────────┘
        raw data         efficiency           ranked
                         factors JSON         recommendations
```

### Agents
1. **Data Collector** — calls PVGIS, Open-Meteo (weather + air quality), NASA POWER, Nominatim
2. **Analyst** — DeepSeek identifies top 4-6 factors hurting efficiency, returns structured JSON
3. **Advisor** — DeepSeek produces ranked recommendations with cost/effort/payback in INR

### APIs Used (all free, no key needed)
- **PVGIS** (EU JRC) — PV potential, optimal tilt, monthly radiation
- **Open-Meteo** — current weather, 7-day forecast
- **Open-Meteo Air Quality** — PM2.5, PM10, dust (for soiling estimates)
- **NASA POWER** — long-term climatology baseline
- **Nominatim** (OpenStreetMap) — reverse geocoding

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set your DeepSeek API key
cp .env.example .env
# edit .env and paste your DEEPSEEK_API_KEY
```

## Run

```bash
# Interactive
python main.py

# Or pass coordinates directly
python main.py 20.9077 70.3626      # Veraval, Gujarat
python main.py 28.6139 77.2090      # Delhi
python main.py 12.9716 77.5946      # Bengaluru
```

## Sample Output

```
╭─────── ☀  Solar Efficiency Report ───────╮
│  Veraval, Gir Somnath, Gujarat, India   │
│  Coordinates: 20.9077, 70.3626          │
╰──────────────────────────────────────────╯

╭─── Efficiency Score ───╮
│  72/100   ██████████████░░░░░░  │
│                                  │
│  Strong irradiance baseline...   │
╰──────────────────────────────────╯

📊 PVGIS Baseline (1 kWp system, optimal tilt)
...

🔬 Efficiency Factors
...

💡 Recommendations
...
```

## Tech Stack

- **LLM:** DeepSeek V4 (via OpenAI-compatible API)
- **Orchestration:** LangGraph
- **HTTP:** requests
- **Terminal UI:** rich
- **Env:** python-dotenv

## Why This Is a Real Project (Not Just an LLM Wrapper)

- ✅ Real data from 4 independent scientific APIs
- ✅ Structured JSON outputs between agents (no hallucination drift)
- ✅ Physics-grounded analysis prompts (known temperature & soiling coefficients)
- ✅ Cost/payback estimates in local currency
- ✅ Graceful degradation if an API fails
