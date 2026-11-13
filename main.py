from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional
from openai import OpenAI
from datetime import datetime
from collections import defaultdict
import csv
import io
import json
import re

app = FastAPI(title="TRACE Backend")

# 내 맥북의 LM Studio 로컬 서버 연결
ai_client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")

MAX_FILES = 36

class EmailDraftRequest(BaseModel):
    name: str
    current_amount: float
    previous_amount: Optional[float] = None
    price_hike: bool = False
    hike_date: Optional[str] = None
    frequency: str = "Monthly"
    action_type: str = "cancel"  # "cancel" 또는 "negotiate"

def normalize_merchant(raw: str) -> str:
    cleaned = re.sub(r"[#*]\w+", "", raw).strip()
    return cleaned or raw.strip()

def format_display_date(date_str: str) -> str:
    s = date_str.strip()[:10]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%b %d, %Y").replace(" 0", " ")
        except ValueError:
            continue
    return s

def fallback_clean_name(raw: str) -> str:
    name = re.sub(r"\.(com|ca|net|org)\b", "", raw, flags=re.IGNORECASE)
    name = re.sub(r"\b(canada|inc|corp|ltd|autopay|bi-weekly|biweekly|digital sub|membership|bill)\b", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[/]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -*")
    titled = name.title()
    replacements = {
        r"\bYoutube\b": "YouTube",
        r"\bNytimes\b": "NYTimes",
        r"\bGoodlife\b": "GoodLife",
        r"\bIcloud\b": "iCloud",
    }
    for pattern, repl in replacements.items():
        titled = re.sub(pattern, repl, titled)
    return titled or raw.title()

def extract_transactions(csv_text: str) -> list[dict]:
    rows = [r for r in csv.reader(io.StringIO(csv_text)) if any(r)]
    if len(rows) < 2:
        return []

    headers = [h.strip().lower() for h in rows[0]]
    date_idx = next((i for i, h in enumerate(headers) if "date" in h), 0)
    desc_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["desc", "merchant", "name", "payee", "memo"])), 1)
    debit_idx = next((i for i, h in enumerate(headers) if any(k in h for k in ["debit", "withdrawal", "outflow"])), None)
    amount_idx = next((i for i, h in enumerate(headers) if "amount" in h), None)

    txns = []
    for r in rows[1:]:
        if len(r) <= max(date_idx, desc_idx):
            continue
        date_str = r[date_idx].strip()
        desc = r[desc_idx].strip()

        raw_amt = ""
        if debit_idx is not None and debit_idx < len(r) and r[debit_idx].strip():
            raw_amt = r[debit_idx].strip()
        elif amount_idx is not None and amount_idx < len(r) and r[amount_idx].strip():
            raw_amt = r[amount_idx].strip()

        if not raw_amt:
            continue
        cleaned_amt = re.sub(r"[^0-9.-]", "", raw_amt)
        if not cleaned_amt:
            continue

        amt = abs(float(cleaned_amt))
        if amt <= 0:
            continue

        txns.append({"date": date_str, "merchant": desc, "amount": amt})
    return txns

def refine_with_local_ai(merchant_names: list[str]) -> dict:
    if not merchant_names:
        return {}

    combined_results = {}
    batch_size = 25

    for i in range(0, len(merchant_names), batch_size):
        batch = merchant_names[i:i + batch_size]
        prompt = f"""Classify each recurring merchant and return a clean, official consumer brand name.
Set "is_subscription" to true ONLY for recurring subscriptions, memberships, software, telecom, utilities, or gym bills.
Set "is_subscription" to false for coffee shops, restaurants, grocery stores, gas stations, retail shopping, or rideshares.
Remove suffixes like .COM, INC, CANADA, AUTOPAY, BI-WEEKLY, MEMBERSHIP, DIGITAL SUB.

Examples:
- "NETFLIX.COM" -> "Netflix"
- "YOUTUBE PREMIUM" -> "YouTube Premium"
- "NYTIMES DIGITAL SUB" -> "NYTimes"
- "GOODLIFE FITNESS BI-WEEKLY" -> "GoodLife Fitness"
- "ROGERS WIRELESS AUTOPAY" -> "Rogers Wireless"
- "SPOTIFY CANADA INC" -> "Spotify"

Merchants: {json.dumps(batch)}

Return ONLY valid JSON:
{{"results": [{{"raw": "NETFLIX.COM", "clean_name": "Netflix", "is_subscription": true}}]}}"""

        try:
            response = ai_client.chat.completions.create(
                model="meta-llama-3.1-8b-instruct",
                messages=[
                    {"role": "system", "content": "Output strictly valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0,
                max_tokens=1200
            )
            raw_output = response.choices[0].message.content or ""
            cleaned = re.sub(r"```(?:json)?|```", "", raw_output).strip()
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                continue

            data = json.loads(re.sub(r",\s*([}\]])", r"\1", match.group(0)))
            items = data.get("results", data if isinstance(data, list) else [])
            for item in items:
                if isinstance(item, dict) and "raw" in item:
                    combined_results[item["raw"]] = item
        except Exception:
            continue

    return combined_results

@app.get("/")
async def serve_ui():
    return FileResponse("index.html")

@app.post("/api/analyze")
async def analyze_statements(files: list[UploadFile] = File(...)):
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"한 번에 최대 {MAX_FILES}개의 CSV 파일만 분석할 수 있습니다.")

    all_txns = []
    seen_in_prior_files = set()

    for file in files:
        if not file.filename.lower().endswith(".csv"):
            raise HTTPException(status_code=400, detail=f"{file.filename}: CSV 파일만 지원합니다.")

        contents = await file.read()
        decoded_text = contents.decode("utf-8-sig", errors="replace")
        file_txns = extract_transactions(decoded_text)

        current_file_keys = set()
        for t in file_txns:
            dedup_key = (t["date"][:10], normalize_merchant(t["merchant"]), round(t["amount"], 2))
            if dedup_key not in seen_in_prior_files:
                all_txns.append(t)
                current_file_keys.add(dedup_key)
        seen_in_prior_files.update(current_file_keys)

    grouped = defaultdict(list)
    for t in all_txns:
        key = normalize_merchant(t["merchant"])
        grouped[key].append(t)

    candidates = []
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: x["date"])
        last_amt = items[-1]["amount"]
        last_billed = format_display_date(items[-1]["date"])

        hike_date = None
        previous_amt = None
        for i in range(1, len(items)):
            if items[i]["amount"] > items[i - 1]["amount"]:
                previous_amt = items[i - 1]["amount"]
                hike_date = format_display_date(items[i]["date"])

        price_hike = hike_date is not None and last_amt > (previous_amt or 0)

        try:
            dates = [datetime.strptime(x["date"][:10], "%Y-%m-%d") for x in items]
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            avg_gap = sum(gaps) / len(gaps)
        except Exception:
            avg_gap = 30

        if 12 <= avg_gap <= 17:
            freq = "Bi-weekly"
        elif avg_gap >= 330:
            freq = "Annual"
        else:
            freq = "Monthly"

        candidates.append({
            "raw_name": items[-1]["merchant"],
            "current_amount": last_amt,
            "previous_amount": previous_amt if price_hike else None,
            "price_hike": price_hike,
            "hike_date": hike_date if price_hike else None,
            "last_billed": last_billed,
            "frequency": freq
        })

    ai_map = refine_with_local_ai([c["raw_name"] for c in candidates])

    subscriptions = []
    total_monthly = 0.0
    total_annual = 0.0
    extra_annual_hikes = 0.0
    hike_count = 0

    for c in candidates:
        ai_info = ai_map.get(c["raw_name"])
        if ai_info and ai_info.get("is_subscription") is False:
            continue

        ai_name = ai_info.get("clean_name") if ai_info else None
        clean_name = ai_name if (ai_name and "." not in ai_name) else fallback_clean_name(c["raw_name"])
        amount = c["current_amount"]

        if c["frequency"] == "Bi-weekly":
            monthly_eq = (amount * 26) / 12
            annual_eq = amount * 26
            prev_annual = (c["previous_amount"] * 26) if c["previous_amount"] else annual_eq
        elif c["frequency"] == "Annual":
            monthly_eq = amount / 12
            annual_eq = amount
            prev_annual = c["previous_amount"] if c["previous_amount"] else annual_eq
        else:
            monthly_eq = amount
            annual_eq = amount * 12
            prev_annual = (c["previous_amount"] * 12) if c["previous_amount"] else annual_eq

        total_monthly += monthly_eq
        total_annual += annual_eq

        if c["price_hike"]:
            hike_count += 1
            extra_annual_hikes += max(0.0, annual_eq - prev_annual)

        subscriptions.append({
            "name": clean_name,
            "current_amount": amount,
            "previous_amount": c["previous_amount"],
            "price_hike": c["price_hike"],
            "hike_date": c["hike_date"],
            "last_billed": c["last_billed"],
            "frequency": c["frequency"],
            "monthly_equivalent": round(monthly_eq, 2)
        })

    subscriptions.sort(key=lambda x: x["monthly_equivalent"], reverse=True)

    return {
        "subscriptions": subscriptions,
        "total_monthly": round(total_monthly, 2),
        "total_annual": round(total_annual, 2),
        "hike_count": hike_count,
        "extra_annual_hikes": round(extra_annual_hikes, 2)
    }

@app.post("/api/draft-email")
async def draft_email(req: EmailDraftRequest):
    is_negotiate = req.action_type == "negotiate" and req.price_hike and req.previous_amount is not None

    if is_negotiate:
        goal = (
            f"Write a polite, firm customer retention email to {req.name} Billing Support. "
            f"Mention that my {req.frequency.lower()} charge increased from ${req.previous_amount:.2f} to ${req.current_amount:.2f}"
            f"{f' on {req.hike_date}' if req.hike_date else ''}. "
            f"Request to restore the previous rate of ${req.previous_amount:.2f} or apply a loyalty discount, "
            f"otherwise I will cancel my subscription before the next billing cycle."
        )
        fallback_subject = f"Billing Inquiry: Rate Increase on My {req.name} Account"
        fallback_body = (
            f"Hello {req.name} Support Team,\n\n"
            f"I recently noticed that my {req.frequency.lower()} billing rate increased from "
            f"${req.previous_amount:.2f} to ${req.current_amount:.2f}"
            f"{f' starting on {req.hike_date}' if req.hike_date else ''}.\n\n"
            f"As a loyal customer, I would like to ask if you can honor my previous rate of "
            f"${req.previous_amount:.2f} or apply a promotional retention discount to my account. "
            f"If the rate cannot be adjusted, please let me know so I can cancel my subscription prior to the next billing date.\n\n"
            f"Thank you for your help,\n[Your Name]\n[Account Email]"
        )
    else:
        hike_note = (
            f" Mention the recent price increase from ${req.previous_amount:.2f} to ${req.current_amount:.2f}"
            f"{f' on {req.hike_date}' if req.hike_date else ''} as the reason."
            if (req.price_hike and req.previous_amount is not None)
            else ""
        )
        goal = (
            f"Write a clear, concise cancellation request email to {req.name} Billing Support to cancel my "
            f"{req.frequency.lower()} subscription (${req.current_amount:.2f}) effective immediately and stop all future auto-renewals.{hike_note} "
            f"Ask for written confirmation of cancellation."
        )
        fallback_subject = f"Subscription Cancellation Request — {req.name}"
        fallback_body = (
            f"Hello {req.name} Support Team,\n\n"
            f"Please cancel my {req.frequency.lower()} subscription (${req.current_amount:.2f}) effective immediately "
            f"and ensure that no further recurring charges are billed to my payment method.\n\n"
            f"Please reply with a written confirmation once the cancellation has been processed.\n\n"
            f"Thank you,\n[Your Name]\n[Account Email]"
        )

    prompt = f"""{goal}

Keep it under 130 words, professional, and ready to send with placeholders [Your Name] and [Account Email].
Format your response EXACTLY like this:
SUBJECT: <subject line here>
BODY:
<email body here>"""

    try:
        response = ai_client.chat.completions.create(
            model="meta-llama-3.1-8b-instruct",
            messages=[
                {"role": "system", "content": "You write concise, effective consumer billing emails."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=400
        )
        raw_text = (response.choices[0].message.content or "").strip()
        subj_match = re.search(r"SUBJECT:\s*(.+)", raw_text, re.IGNORECASE)
        body_match = re.search(r"BODY:\s*([\s\S]+)", raw_text, re.IGNORECASE)

        if subj_match and body_match:
            subject = subj_match.group(1).strip().strip("*")
            body = body_match.group(1).strip()
            return {"subject": subject, "body": body}
    except Exception:
        pass

    return {"subject": fallback_subject, "body": fallback_body}
