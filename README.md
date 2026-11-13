# TRACE

**Find what quietly drains you.**  
A privacy-first, local-AI subscription and stealth price-hike detector wrapped in a minimalist liquid-glass interface.

TRACE processes your bank and credit card CSV statements strictly in memory (RAM) and uses a local LLM via LM Studio—meaning your sensitive financial records never leave your MacBook.

---

### Key Features

* **100% Local & In-Memory Privacy:** Zero bank logins, zero database storage, and zero cloud API calls. Uploaded CSV files are parsed in memory and discarded immediately.
* **Multi-Statement Analysis & Deduplication:** Drop up to 36 CSV statements at once (up to 3 years of history). Automatically deduplicates overlapping transactions across different bank and credit card exports.
* **Stealth Price Hike Detection:** Identifies recurring charges that quietly increased over time, pinpointing the exact billing date the hike occurred and calculating the annualized extra cost.
* **Hybrid Precision Engine:** Combines deterministic Python math for accurate monthly/annual normalization (including bi-weekly and annual billing cycles) with local Llama 3.1 inference for merchant classification and brand cleanup.
* **One-Click AI Action Drafts:** Generates ready-to-send cancellation or rate-negotiation emails tailored with your exact billing amounts and price-hike dates.

---

### Tech Stack

* **Backend:** Python, FastAPI, Uvicorn
* **Frontend:** HTML5, Tailwind CSS, Vanilla JavaScript (Liquid-Glass UI)
* **Local AI Runtime:** LM Studio (`meta-llama-3.1-8b-instruct` via OpenAI-compatible local endpoint)

---

### Quickstart

#### 1. Start Your Local LLM
1. Open **LM Studio** and load **`meta-llama-3.1-8b-instruct`** (or another Llama 3.1 Instruct GGUF model).
2. Navigate to the **Local Server** tab and click **Start Server** (default port `1234`).

#### 2. Clone & Install Dependencies
```bash
git clone [https://github.com/YOUR_USERNAME/trace-saas.git](https://github.com/YOUR_USERNAME/trace-saas.git)
cd trace-saas

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

#### 3. Run TRACE
```bash
uvicorn main:app --reload
```
Open **`http://127.0.0.1:8000`** in your browser and drag & drop your bank statement `.CSV` files.

---

### License

MIT License. Free to use, modify, and self-host.
