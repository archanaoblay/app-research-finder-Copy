# ============================================================
# APP RESEARCH FINDER - CLOUD WORKER
# Preserves the original LinkedIn discovery, scoring and confidence logic.
# Input/output and progress are handled by Supabase instead of Colab files.
# ============================================================

import os
import re
import time
import random
import logging
import warnings
import urllib.parse
from typing import Any

import requests
import tldextract
from bs4 import BeautifulSoup
from ddgs import DDGS

warnings.filterwarnings("ignore")
logging.getLogger("ddgs").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)

# ----------------------------- SETTINGS -----------------------------

REQUEST_TIMEOUT = 6
SEARCH_TIMEOUT = 6
MAX_RESULTS = 6
MIN_APP_DELAY = 0.5
MAX_APP_DELAY = 1.0
SEARCH_DELAY = 0.15
EARLY_STOP_SCORE = 12
BATCH_SIZE = 25

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
JOB_ID = os.environ["JOB_ID"]

REST_URL = f"{SUPABASE_URL}/rest/v1"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=minimal",
}

session = requests.Session()
session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/155.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
})

WEBSITE_LINKEDIN_CACHE = {}
SEARCH_CACHE = {}
COMPANY_RESULT_CACHE = {}
LINKEDIN_WEBSITE_CACHE = {}

FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in",
    "hotmail.com", "outlook.com", "live.com", "icloud.com",
    "proton.me", "protonmail.com", "aol.com", "mail.com",
    "gmx.com", "rediffmail.com"
}

OUTPUT_COLUMNS = [
    "Bundle ID", "Developer 1", "Developer 2", "Website", "LinkedIn",
    "LinkedIn Website", "Website Match", "Found Via", "Matched Signal",
    "Score", "Confidence", "Reasons", "Cache"
]


# ----------------------------- SUPABASE -----------------------------

def db_request(method, table, params=None, payload=None):
    url = f"{REST_URL}/{table}"
    response = requests.request(
        method,
        url,
        headers=HEADERS,
        params=params,
        json=payload,
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"Supabase {method} {table} failed "
            f"({response.status_code}): {response.text[:1000]}"
        )
    if response.text.strip():
        return response.json()
    return None


def update_job(status=None, processed_rows=None):
    payload = {}
    if status is not None:
        payload["status"] = status
    if processed_rows is not None:
        payload["processed_rows"] = processed_rows
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    db_request(
        "PATCH",
        "research_jobs",
        params={"id": f"eq.{JOB_ID}"},
        payload=payload,
    )


def fetch_pending_rows():
    # Include both common initial states so the worker can process rows
    # created by the app regardless of whether it labels them pending or queued.
    params = {
        "select": (
            "id,job_id,bundle_id,developer_1,developer_2,website,"
            "support_email,developer_email,input_data,status"
        ),
        "job_id": f"eq.{JOB_ID}",
        "status": "in.(pending,queued)",
        "order": "created_at.asc",
        "limit": str(BATCH_SIZE),
    }
    return db_request("GET", "research_apps", params=params) or []


def save_app_result(app_id, result, status="completed", error_message=None):
    payload = {
        "status": status,
        "linkedin": result.get("LinkedIn", ""),
        "linkedin_website": result.get("LinkedIn Website", ""),
        "website_match": result.get("Website Match", "NOT VERIFIED"),
        "found_via": result.get("Found Via", ""),
        "matched_signal": result.get("Matched Signal", ""),
        "score": int(result.get("Score", 0) or 0),
        "confidence": result.get("Confidence", ""),
        "reasons": result.get("Reasons", ""),
        "cache_status": result.get("Cache", "MISS"),
        "error_message": error_message,
        "processed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    db_request(
        "PATCH",
        "research_apps",
        params={"id": f"eq.{app_id}"},
        payload=payload,
    )


# ----------------------------- ORIGINAL HELPERS -----------------------------

def clean_input(value):
    if value is None:
        return ""
    value = str(value).strip()
    if value.lower() in ["nan", "none", "null", "n/a", "na", "-"]:
        return ""
return value


def normalize_website(url):
    url = clean_input(url)
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urllib.parse.urlparse(url)
        if not parsed.netloc:
            return ""
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    except Exception:
        return ""


def root_domain(url):
    url = clean_input(url)
    if not url:
        return ""
    try:
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urllib.parse.urlparse(url)
        host = parsed.netloc.lower().split("@")[-1].split(":")[0]
        extracted = tldextract.extract(host)
        if not extracted.domain:
            return ""
        if not extracted.suffix:
            return extracted.domain.lower()
        return (extracted.domain + "." + extracted.suffix).lower()
    except Exception:
        return ""


def normalize_email(email):
    email = clean_input(email).lower().strip()
    if not email:
        return ""
    match = re.search(
        r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
        email, re.I
    )
    return match.group(0).lower() if match else email


def email_domain(email):
    email = normalize_email(email)
    return email.split("@")[-1].strip().lower() if "@" in email else ""


def email_username(email):
    email = normalize_email(email)
    return email.split("@")[0].lower() if "@" in email else ""


def extract_linkedin_company(url):
    url = clean_input(url).replace("\\/", "/")
    if not url:
        return ""
    patterns = [
        r"https?://(?:www\.)?linkedin\.com/company/[A-Za-z0-9%_\-./]+",
        r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/company/[A-Za-z0-9%_\-./]+",
    ]
    for pattern in patterns:
        match = re.search(pattern, url, re.I)
        if match:
            result = match.group(0).rstrip(".,);]}>'\"")
            match2 = re.match(
                r"(https?://(?:www\.)?linkedin\.com/company/[^/?#]+)",
                result, re.I
            )
            if match2:
                result = match2.group(1)
            return result.rstrip("/")
    return ""


def fetch_url(url):
    try:
        response = session.get(
            url, timeout=REQUEST_TIMEOUT, allow_redirects=True
        )
        return None if response.status_code >= 400 else response
    except Exception:
        return None


def search_website_directly(website):
    website = normalize_website(website)
    if not website:
        return []
    if website in WEBSITE_LINKEDIN_CACHE:
        return WEBSITE_LINKEDIN_CACHE[website]

    found = []
    urls_to_check = [website]
    for path in ["about", "about-us", "aboutus", "company", "contact", "contact-us"]:
        urls_to_check.append(website.rstrip("/") + "/" + path)

    for url in dict.fromkeys(urls_to_check):
        try:
            response = fetch_url(url)
            if response is None:
                continue
            linkedin = extract_linkedin_company(response.text)
            if linkedin:
                found.append({
                    "linkedin": linkedin,
                    "source_url": url,
                    "method": "WEBSITE_DIRECT",
                })
                break

            soup = BeautifulSoup(response.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                linkedin = extract_linkedin_company(anchor.get("href", ""))
                if linkedin:
                    found.append({
                        "linkedin": linkedin,
                        "source_url": url,
                        "method": "WEBSITE_DIRECT",
                    })
                    break
            if found:
                break
        except Exception:
            continue

    WEBSITE_LINKEDIN_CACHE[website] = found
    return found


def run_search(query, max_results=MAX_RESULTS):
    query = clean_input(query)
    if not query:
        return []
    cache_key = (query.lower(), max_results)
    if cache_key in SEARCH_CACHE:
        return SEARCH_CACHE[cache_key]
    try:
        time.sleep(SEARCH_DELAY)
        ddgs = DDGS(timeout=SEARCH_TIMEOUT)
        results = ddgs.text(
            query,
            region="wt-wt",
            safesearch="off",
            max_results=max_results,
            backend="google, brave, duckduckgo",
        )
        results = list(results or [])
        SEARCH_CACHE[cache_key] = results
        return results
    except Exception as exc:
        print(f"Search unavailable for query ({type(exc).__name__}).")
        SEARCH_CACHE[cache_key] = []
        return []


def linkedin_from_search_results(results):
    candidates = []
    for result in results:
        if not isinstance(result, dict):
            continue
        title = clean_input(result.get("title", ""))
        href = clean_input(result.get("href", ""))
        body = clean_input(result.get("body", ""))
        linkedin = extract_linkedin_company(title + " " + href + " " + body)
        if linkedin:
            candidates.append({
                "linkedin": linkedin,
                "title": title,
                "href": href,
                "body": body,
            })
    return candidates


def linkedin_slug(linkedin):
    linkedin = clean_input(linkedin)
    match = re.search(r"linkedin\.com/company/([^/?#]+)", linkedin, re.I)
    if not match:
        return ""
    return (
        urllib.parse.unquote(match.group(1))
        .replace("-", " ").replace("_", " ").strip().lower()
    )


def normalize_name(value):
    value = clean_input(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


# ----------------------------- ORIGINAL SCORING -----------------------------

def score_candidate(
    linkedin, developer1, developer2, website, support_email,
    developer_email, title="", href="", body="", found_via=""
):
    score = 0
    reasons = []
    slug = linkedin_slug(linkedin)
    d1 = normalize_name(developer1)
    d2 = normalize_name(developer2)
    website_domain = root_domain(website)
    support_domain = email_domain(support_email)
    developer_email_domain = email_domain(developer_email)
    support_username = email_username(support_email)
    developer_username = email_username(developer_email)
    all_text = normalize_name(" ".join([title, href, body, slug]))

    if website_domain:
        clean_domain = website_domain.replace(".", " ").replace("-", " ")
        if website_domain in href.lower() or clean_domain in all_text:
            score += 8
            reasons.append("Website domain matches candidate")

    if d1 and len(d1) >= 3:
        words = [x for x in d1.split() if len(x) >= 3]
        matched = sum(1 for word in words if word in all_text)
        if matched >= 2:
            score += 8
            reasons.append("Developer 1 strongly matches")
        elif matched == 1:
            score += 4
            reasons.append("Developer 1 partially matches")

if d2 and len(d2) >= 3:
        words = [x for x in d2.split() if len(x) >= 3]
        matched = sum(1 for word in words if word in all_text)
        if matched >= 2:
            score += 6
            reasons.append("Developer 2 strongly matches")
        elif matched == 1:
            score += 3
            reasons.append("Developer 2 partially matches")

    if support_domain and support_domain not in FREE_EMAIL_DOMAINS:
        clean_domain = support_domain.replace(".", " ").replace("-", " ")
        if support_domain in href.lower() or clean_domain in all_text:
            score += 7
            reasons.append("Support email domain matches")

    if developer_email_domain and developer_email_domain not in FREE_EMAIL_DOMAINS:
        clean_domain = developer_email_domain.replace(".", " ").replace("-", " ")
        if developer_email_domain in href.lower() or clean_domain in all_text:
            score += 7
            reasons.append("Developer email domain matches")

    for username, label in [
        (support_username, "Support email username"),
        (developer_username, "Developer email username"),
    ]:
        if username and len(username) >= 4 and username in all_text:
            score += 2
            reasons.append(label + " matches")

    if "linkedin.com/company/" in linkedin.lower():
        score += 2
        reasons.append("Valid LinkedIn company URL")

    if found_via == "WEBSITE_DIRECT":
        score += 10
        reasons.append("LinkedIn found directly on developer website")

    return score, reasons


def find_candidates(developer1, developer2, website, support_email, developer_email):
    candidates = {}
    domain = root_domain(website)
    queries = []

    if domain:
        queries.extend([
            (f'"{domain}" LinkedIn company', "WEBSITE_DOMAIN"),
            (f'site:linkedin.com/company "{domain}"', "WEBSITE_DOMAIN_LINKEDIN"),
        ])
    if developer1:
        queries.extend([
            (f'"{developer1}" LinkedIn company', "DEVELOPER_1"),
            (f'site:linkedin.com/company "{developer1}"', "DEVELOPER_1_LINKEDIN"),
        ])
    if developer2:
        queries.extend([
            (f'"{developer2}" LinkedIn company', "DEVELOPER_2"),
            (f'site:linkedin.com/company "{developer2}"', "DEVELOPER_2_LINKEDIN"),
        ])

    support_email = normalize_email(support_email)
    support_domain = email_domain(support_email)
    if support_domain and support_domain not in FREE_EMAIL_DOMAINS:
        queries.append((f'"{support_domain}" LinkedIn company', "SUPPORT_EMAIL_DOMAIN"))

    developer_email = normalize_email(developer_email)
    developer_domain = email_domain(developer_email)
    if developer_domain and developer_domain not in FREE_EMAIL_DOMAINS:
        queries.append((f'"{developer_domain}" LinkedIn company', "DEVELOPER_EMAIL_DOMAIN"))

    for query, found_via in queries:
        print(f"      Search: {query}")
        results = linkedin_from_search_results(run_search(query, MAX_RESULTS))
        for item in results:
            linkedin = item["linkedin"]
            score, reasons = score_candidate(
                linkedin=linkedin,
                developer1=developer1,
                developer2=developer2,
                website=website,
                support_email=support_email,
                developer_email=developer_email,
                title=item["title"],
                href=item["href"],
                body=item["body"],
                found_via=found_via,
            )
            if linkedin not in candidates:
                candidates[linkedin] = {
                    "linkedin": linkedin,
                    "score": score,
                    "reasons": reasons,
                    "found_via": found_via,
                    "title": item["title"],
                    "href": item["href"],
                    "body": item["body"],
                    "matched_signal": query,
                }
            else:
                candidates[linkedin]["score"] += score
                candidates[linkedin]["reasons"].extend(reasons)

        best_score = max((x["score"] for x in candidates.values()), default=0)
        if best_score >= EARLY_STOP_SCORE:
            print(f"      Strong candidate found (score {best_score})")
            break

    return list(candidates.values())


def find_linkedin_website(linkedin):
    linkedin = clean_input(linkedin)
    if not linkedin:
        return ""
    if linkedin in LINKEDIN_WEBSITE_CACHE:
        return LINKEDIN_WEBSITE_CACHE[linkedin]

    slug = linkedin_slug(linkedin)
    if not slug:
        LINKEDIN_WEBSITE_CACHE[linkedin] = ""
        return ""

    queries = [
        f'"{linkedin}" website',
        f'"{slug}" company website',
        f'"{slug}" official website',
    ]
    found_website = ""
    skip_domains = {
        "facebook.com", "instagram.com", "twitter.com", "x.com",
        "youtube.com", "tiktok.com", "crunchbase.com", "glassdoor.com",
        "indeed.com",
    }

    for query in queries:
        for result in run_search(query, 5):
            href = clean_input(result.get("href", ""))
            if not href or "linkedin.com" in href.lower():
                continue
            href_domain = root_domain(href)
            if not href_domain or href_domain in skip_domains:
                continue
            if href.startswith(("http://", "https://")):
                found_website = normalize_website(href)
                if found_website:
                    break
        if found_website:
            break

    LINKEDIN_WEBSITE_CACHE[linkedin] = found_website
    return found_website


def website_match(original_website, linkedin_website):
    original_domain = root_domain(original_website)
    linkedin_domain = root_domain(linkedin_website)
    if not original_domain or not linkedin_domain:
        return "NOT VERIFIED"
    return "YES" if original_domain == linkedin_domain else "NO"


def calculate_confidence(score, linkedin_website, match):
    if linkedin_website and match == "YES":
        return "VERY HIGH"
    if linkedin_website and match == "NO":
        if score >= 15:
            return "HIGH - WEBSITE MISMATCH"
        if score >= 8:
            return "MEDIUM - WEBSITE MISMATCH"
        return "LOW - WEBSITE MISMATCH"
    if score >= 20:
        return "HIGH"
    if score >= 12:
        return "MEDIUM"
    if score >= 6:
        return "LOW - TOP MATCH"
    return "VERY LOW - TOP MATCH"


# ----------------------------- PROCESS ONE APP -----------------------------

def process_app(bundle_id, developer1, developer2, website, support_email, developer_email):
    bundle_id = clean_input(bundle_id)
    developer1 = clean_input(developer1)
    developer2 = clean_input(developer2)
    website = normalize_website(website)
    support_email = normalize_email(support_email)
    developer_email = normalize_email(developer_email)

    cache_key = (
        bundle_id.lower(), developer1.lower(), developer2.lower(),
        website.lower(), support_email.lower(), developer_email.lower()
    )
    if cache_key in COMPANY_RESULT_CACHE:
        cached = COMPANY_RESULT_CACHE[cache_key].copy()
        cached["Cache"] = "HIT"
        return cached

    direct_results = search_website_directly(website) if website else []

    if direct_results:
        linkedin = direct_results[0]["linkedin"]
        found_via = "WEBSITE_DIRECT"
        score, reasons = score_candidate(
            linkedin=linkedin,
            developer1=developer1,
            developer2=developer2,
            website=website,
            support_email=support_email,
            developer_email=developer_email,
            title="",
            href=linkedin,
            body="",
            found_via="WEBSITE_DIRECT",
        )
        # Preserve the original script's additional +10 for direct discovery.
        score += 10
        if "LinkedIn found directly on developer website" not in reasons:
            reasons.append("LinkedIn found directly on developer website")
        matched_signal = website
    else:
        candidates = find_candidates(
            developer1, developer2, website, support_email, developer_email
        )
        if not candidates:
            result = {
                "Bundle ID": bundle_id,
                "Developer 1": developer1,
                "Developer 2": developer2,
                "Website": website,
                "LinkedIn": "NOT FOUND",
                "LinkedIn Website": "",
                "Website Match": "NOT VERIFIED",
                "Found Via": "",
                "Matched Signal": "",
                "Score": 0,
                "Confidence": "NOT FOUND",
                "Reasons": "No LinkedIn company candidate found",
                "Cache": "MISS",
            }
            COMPANY_RESULT_CACHE[cache_key] = result.copy()
            return result

        candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)
        best = candidates[0]
        linkedin = best["linkedin"]
        score = best["score"]
        reasons = list(dict.fromkeys(best["reasons"]))
        found_via = best["found_via"]
        matched_signal = best["matched_signal"]

    linkedin_website = find_linkedin_website(linkedin)
    match = website_match(website, linkedin_website)
    confidence = calculate_confidence(score, linkedin_website, match)

    if match == "YES":
        reasons.append("LinkedIn website matches original website")
    elif match == "NO":
        reasons.append("LinkedIn website does not match original website")
    else:
        reasons.append("LinkedIn website could not be independently verified")

    result = {
        "Bundle ID": bundle_id,
        "Developer 1": developer1,
        "Developer 2": developer2,
        "Website": website,
        "LinkedIn": linkedin,
        "LinkedIn Website": linkedin_website,
        "Website Match": match,
        "Found Via": found_via,
        "Matched Signal": matched_signal,
        "Score": score,
        "Confidence": confidence,
        "Reasons": " | ".join(dict.fromkeys(reasons)),
        "Cache": "MISS",
    }
    COMPANY_RESULT_CACHE[cache_key] = result.copy()
    return result


# ----------------------------- MAIN WORKER LOOP -----------------------------

def main():
    print(f"Starting worker for job {JOB_ID}")
    update_job(status="running")

    processed_this_run = 0

    while True:
        rows = fetch_pending_rows()
        if not rows:
            break

        for row in rows:
            app_id = row["id"]
            bundle_id = clean_input(row.get("bundle_id"))
            developer1 = clean_input(row.get("developer_1"))
            developer2 = clean_input(row.get("developer_2"))
            website = clean_input(row.get("website"))
            support_email = clean_input(row.get("support_email"))
            developer_email = clean_input(row.get("developer_email"))

            print(f"\n[{processed_this_run + 1}] Processing {bundle_id}")

            try:
                result = process_app(
                    bundle_id=bundle_id,
                    developer1=developer1,
                    developer2=developer2,
                    website=website,
                    support_email=support_email,
                    developer_email=developer_email,
                )
                save_app_result(app_id, result, status="completed")
            except Exception as exc:
                error_text = f"{type(exc).__name__}: {str(exc)[:800]}"
                print(f"App error for {bundle_id}: {error_text}")
                error_result = {
                    "LinkedIn": "",
                    "LinkedIn Website": "",
                    "Website Match": "NOT VERIFIED",
                    "Found Via": "",
                    "Matched Signal": "",
                    "Score": 0,
                    "Confidence": "ERROR",
                    "Reasons": error_text,
                    "Cache": "ERROR",
                }
                # Mark this row as error so the worker does not retry it forever.
                save_app_result(
                    app_id, error_result, status="error",
                    error_message=error_text
                )

            processed_this_run += 1
            update_job(processed_rows=processed_this_run)

            time.sleep(random.uniform(MIN_APP_DELAY, MAX_APP_DELAY))

    # Count completed and errored rows for this job.
    all_rows = db_request(
        "GET",
        "research_apps",
        params={
            "select": "id,status",
            "job_id": f"eq.{JOB_ID}",
            "limit": "10000",
        },
    ) or []
    finished = sum(1 for item in all_rows if item.get("status") in ("completed", "error"))
    total = len(all_rows)

    final_status = "completed" if finished >= total else "failed"
    update_job(status=final_status, processed_rows=finished)
    print(f"Worker finished. {finished}/{total} rows completed or errored.")


if __name__ == "__main__":
    main()
