# ============================================================
# APP RESEARCH FINDER - CLOUD WORKER
# Original LinkedIn discovery, scoring, confidence and matching logic
# ported from BULK LINKEDIN COMPANY FINDER.
# Only Colab file handling is replaced by Supabase/GitHub Actions.
# ============================================================

import os
import re
import time
import random
import logging
import warnings
import urllib.parse

import pandas as pd
import requests
import tldextract
from bs4 import BeautifulSoup
from ddgs import DDGS
from google_play_scraper import app as google_play_app

# ============================================================
# 3. SUPPRESS WARNINGS / LOGS
# ============================================================

warnings.filterwarnings("ignore")

logging.getLogger("ddgs").setLevel(logging.ERROR)
logging.getLogger("ddgs.ddgs").setLevel(logging.ERROR)
logging.getLogger("urllib3").setLevel(logging.ERROR)


# ============================================================
# 4. SETTINGS
# ============================================================

REQUEST_TIMEOUT = 6
SEARCH_TIMEOUT = 6

MAX_RESULTS = 6

# ------------------------------------------------------------
# FASTER DELAYS
# ------------------------------------------------------------

MIN_APP_DELAY = 0.5
MAX_APP_DELAY = 1.0

SEARCH_DELAY = 0.15

# ------------------------------------------------------------
# SEARCH CONTROL
# ------------------------------------------------------------

EARLY_STOP_SCORE = 12

# ------------------------------------------------------------
# SAVE EVERY 10 APPS
# ------------------------------------------------------------

SAVE_EVERY = 10

PROGRESS_FILE = (
    "/content/"
    "LinkedIn_Company_Finder_Progress.xlsx"
)

FINAL_FILE = (
    "/content/"
    "LinkedIn_Company_Finder_Final.xlsx"
)

# ------------------------------------------------------------
# WEBSITE PAGES
# ------------------------------------------------------------

WEBSITE_PATHS = [
    "",
    "about",
    "about-us",
    "aboutus",
    "company",
    "contact",
    "contact-us"
]


# ============================================================
# 5. SESSION
# ============================================================

session = requests.Session()

session.headers.update({

    "User-Agent": (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/155.0 Safari/537.36"
    ),

    "Accept-Language":
        "en-US,en;q=0.9"

})


# ============================================================
# 6. CACHES
# ============================================================

WEBSITE_LINKEDIN_CACHE = {}

SEARCH_CACHE = {}

COMPANY_RESULT_CACHE = {}

LINKEDIN_WEBSITE_CACHE = {}


# ============================================================
# 7. FREE EMAIL DOMAINS
# ============================================================

FREE_EMAIL_DOMAINS = {

    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "yahoo.co.in",
    "hotmail.com",
    "outlook.com",
    "live.com",
    "icloud.com",
    "proton.me",
    "protonmail.com",
    "aol.com",
    "mail.com",
    "gmx.com",
    "rediffmail.com"

}


# ============================================================
# 8. CLEAN INPUT
# ============================================================

def clean_input(value):

    if value is None:
        return ""

    try:

        if pd.isna(value):
            return ""

    except Exception:
        pass

    value = str(value).strip()

    if value.lower() in [
        "nan",
        "none",
        "null",
        "n/a",
        "na",
        "-"
    ]:

        return ""

    return value


# ============================================================
# 9. NORMALIZE WEBSITE
# ============================================================

def normalize_website(url):

    url = clean_input(url)

    if not url:
        return ""

    if not url.startswith(
        ("http://", "https://")
    ):

        url = "https://" + url

    try:

        parsed = urllib.parse.urlparse(
            url
        )

        if not parsed.netloc:
            return ""

        return (
            f"{parsed.scheme}://"
            f"{parsed.netloc}"
        ).rstrip("/")

    except Exception:

        return ""


# ============================================================
# 10. ROOT DOMAIN
# ============================================================

def root_domain(url):

    url = clean_input(url)

    if not url:
        return ""

    try:

        if not url.startswith(
            ("http://", "https://")
        ):

            url = "https://" + url

        parsed = urllib.parse.urlparse(
            url
        )

        host = parsed.netloc.lower()

        host = host.split("@")[-1]
        host = host.split(":")[0]

        extracted = tldextract.extract(
            host
        )

        if not extracted.domain:
            return ""

        if not extracted.suffix:
            return extracted.domain.lower()

        return (
            extracted.domain
            + "."
            + extracted.suffix
        ).lower()

    except Exception:

        return ""


# ============================================================
# 11. NORMALIZE EMAIL
# ============================================================

def normalize_email(email):

    email = clean_input(email)

    if not email:
        return ""

    email = email.lower().strip()

    match = re.search(
        r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
        email,
        re.I
    )

    if match:
        return match.group(0).lower()

    return email


# ============================================================
# 12. EMAIL DOMAIN
# ============================================================

def email_domain(email):

    email = normalize_email(
        email
    )

    if "@" not in email:
        return ""

    return (
        email.split("@")[-1]
        .strip()
        .lower()
    )


# ============================================================
# 13. EMAIL USERNAME
# ============================================================

def email_username(email):

    email = normalize_email(
        email
    )

    if "@" not in email:
        return ""

    return (
        email.split("@")[0]
        .lower()
    )


# ============================================================
# 14. LINKEDIN COMPANY URL
# ============================================================

def extract_linkedin_company(url):

    url = clean_input(url)

    if not url:
        return ""

    url = url.replace(
        "\\/",
        "/"
    )

    patterns = [

        r"https?://(?:www\.)?"
        r"linkedin\.com/company/"
        r"[A-Za-z0-9%_\-./]+",

        r"https?://(?:[a-z]{2,3}\.)?"
        r"linkedin\.com/company/"
        r"[A-Za-z0-9%_\-./]+"

    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            url,
            re.I
        )

        if match:

            result = match.group(0)

            result = result.rstrip(
                ".,);]}>'\""
            )

            match2 = re.match(
                r"(https?://(?:www\.)?"
                r"linkedin\.com/company/"
                r"[^/?#]+)",
                result,
                re.I
            )

            if match2:

                result = (
                    match2.group(1)
                )

            return result.rstrip("/")

    return ""


# ============================================================
# 15. FETCH WEBSITE
# ============================================================

def fetch_url(url):

    try:

        response = session.get(

            url,

            timeout=REQUEST_TIMEOUT,

            allow_redirects=True

        )

        if response.status_code >= 400:
            return None

        return response

    except Exception:

        return None


# ============================================================
# 16. FIND LINKEDIN DIRECTLY FROM WEBSITE
# ============================================================

def search_website_directly(
    website
):

    website = normalize_website(
        website
    )

    if not website:
        return []

    if website in WEBSITE_LINKEDIN_CACHE:

        return WEBSITE_LINKEDIN_CACHE[
            website
        ]

    found = []

    urls_to_check = []

    # Homepage
    urls_to_check.append(
        website
    )

    # Internal pages
    for path in WEBSITE_PATHS:

        if path == "":
            continue

        urls_to_check.append(

            website.rstrip("/")
            + "/"
            + path

        )

    urls_to_check = list(
        dict.fromkeys(
            urls_to_check
        )
    )

    # --------------------------------------------------------
    # CHECK WEBSITE PAGES
    # --------------------------------------------------------

    for url in urls_to_check:

        try:

            response = fetch_url(
                url
            )

            if response is None:
                continue

            html = response.text

            # Raw HTML
            linkedin = (
                extract_linkedin_company(
                    html
                )
            )

            if linkedin:

                found.append({

                    "linkedin":
                        linkedin,

                    "source_url":
                        url,

                    "method":
                        "WEBSITE_DIRECT"

                })

                break

            # Actual links
            soup = BeautifulSoup(
                html,
                "html.parser"
            )

            for a in soup.find_all(
                "a",
                href=True
            ):

                href = a.get(
                    "href",
                    ""
                )

                linkedin = (
                    extract_linkedin_company(
                        href
                    )
                )

                if linkedin:

                    found.append({

                        "linkedin":
                            linkedin,

                        "source_url":
                            url,

                        "method":
                            "WEBSITE_DIRECT"

                    })

                    break

            if found:
                break

        except Exception:
            continue

    WEBSITE_LINKEDIN_CACHE[
        website
    ] = found

    return found


# ============================================================
# 17. SEARCH ENGINE
# ============================================================

def run_search(
    query,
    max_results=MAX_RESULTS
):

    query = clean_input(
        query
    )

    if not query:
        return []

    cache_key = (
        query.lower(),
        max_results
    )

    if cache_key in SEARCH_CACHE:

        return SEARCH_CACHE[
            cache_key
        ]

    try:

        time.sleep(
            SEARCH_DELAY
        )

        ddgs = DDGS(
            timeout=SEARCH_TIMEOUT
        )

        results = ddgs.text(

            query,

            region="wt-wt",

            safesearch="off",

            max_results=max_results,

            backend=(
                "yahoo"
            )

        )

        results = list(
            results or []
        )

        SEARCH_CACHE[
            cache_key
        ] = results

        return results

    except Exception as exc:

        # Keep the original fallback behaviour, but expose the real search
        # provider error in Actions logs instead of silently reporting NOT FOUND.
        print(
            f"      SEARCH ERROR for query {query!r}: "
            f"{type(exc).__name__}: {str(exc)[:500]}"
        )

        SEARCH_CACHE[
            cache_key
        ] = []

        return []


# ============================================================
# 18. EXTRACT LINKEDIN FROM SEARCH RESULTS
# ============================================================

def linkedin_from_search_results(
    results
):

    candidates = []

    for result in results:

        if not isinstance(
            result,
            dict
        ):
            continue

        title = clean_input(
            result.get(
                "title",
                ""
            )
        )

        href = clean_input(
            result.get(
                "href",
                ""
            )
        )

        body = clean_input(
            result.get(
                "body",
                ""
            )
        )

        text = (
            title
            + " "
            + href
            + " "
            + body
        )

        linkedin = (
            extract_linkedin_company(
                text
            )
        )

        if linkedin:

            candidates.append({

                "linkedin":
                    linkedin,

                "title":
                    title,

                "href":
                    href,

                "body":
                    body

            })

    return candidates


# ============================================================
# 19. LINKEDIN SLUG
# ============================================================

def linkedin_slug(
    linkedin
):

    linkedin = clean_input(
        linkedin
    )

    if not linkedin:
        return ""

    match = re.search(

        r"linkedin\.com/company/"
        r"([^/?#]+)",

        linkedin,

        re.I

    )

    if not match:
        return ""

    return (

        urllib.parse.unquote(
            match.group(1)
        )

        .replace("-", " ")

        .replace("_", " ")

        .strip()

        .lower()

    )


# ============================================================
# 20. NORMALIZE NAME
# ============================================================

def normalize_name(
    value
):

    value = clean_input(
        value
    )

    value = value.lower()

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


# ============================================================
# 21. SCORE CANDIDATE
# ============================================================

def score_candidate(

    linkedin,

    developer1,

    developer2,

    website,

    support_email,

    developer_email,

    title="",

    href="",

    body="",

    found_via=""

):

    score = 0

    reasons = []

    linkedin_slug_value = (
        linkedin_slug(
            linkedin
        )
    )

    d1 = normalize_name(
        developer1
    )

    d2 = normalize_name(
        developer2
    )

    website_domain = root_domain(
        website
    )

    support_domain = email_domain(
        support_email
    )

    developer_email_domain = (
        email_domain(
            developer_email
        )
    )

    support_username = (
        email_username(
            support_email
        )
    )

    developer_username = (
        email_username(
            developer_email
        )
    )

    all_text = normalize_name(

        " ".join([

            title,

            href,

            body,

            linkedin_slug_value

        ])

    )

    # --------------------------------------------------------
    # WEBSITE DOMAIN
    # --------------------------------------------------------

    if website_domain:

        website_domain_clean = (

            website_domain

            .replace(
                ".",
                " "
            )

            .replace(
                "-",
                " "
            )

        )

        if (

            website_domain
            in href.lower()

        ):

            score += 8

            reasons.append(
                "Website domain matches candidate"
            )

        elif (
            website_domain_clean
            in all_text
        ):

            score += 8

            reasons.append(
                "Website domain appears in result"
            )

    # --------------------------------------------------------
    # DEVELOPER 1
    # --------------------------------------------------------

    if d1 and len(d1) >= 3:

        d1_words = [

            x

            for x in d1.split()

            if len(x) >= 3

        ]

        matched = sum(

            1

            for word in d1_words

            if word in all_text

        )

        if matched >= 2:

            score += 8

            reasons.append(
                "Developer 1 strongly matches"
            )

        elif matched == 1:

            score += 4

            reasons.append(
                "Developer 1 partially matches"
            )

    # --------------------------------------------------------
    # DEVELOPER 2
    # --------------------------------------------------------

    if d2 and len(d2) >= 3:

        d2_words = [

            x

            for x in d2.split()

            if len(x) >= 3

        ]

        matched = sum(

            1

            for word in d2_words

            if word in all_text

        )

        if matched >= 2:

            score += 6

            reasons.append(
                "Developer 2 strongly matches"
            )

        elif matched == 1:

            score += 3

            reasons.append(
                "Developer 2 partially matches"
            )

    # --------------------------------------------------------
    # SUPPORT EMAIL DOMAIN
    # --------------------------------------------------------

    if (

        support_domain

        and

        support_domain
        not in FREE_EMAIL_DOMAINS

    ):

        support_domain_clean = (

            support_domain

            .replace(
                ".",
                " "
            )

            .replace(
                "-",
                " "
            )

        )

        if (

            support_domain
            in href.lower()

            or

            support_domain_clean
            in all_text

        ):

            score += 7

            reasons.append(
                "Support email domain matches"
            )

    # --------------------------------------------------------
    # DEVELOPER EMAIL DOMAIN
    # --------------------------------------------------------

    if (

        developer_email_domain

        and

        developer_email_domain
        not in FREE_EMAIL_DOMAINS

    ):

        dev_domain_clean = (

            developer_email_domain

            .replace(
                ".",
                " "
            )

            .replace(
                "-",
                " "
            )

        )

        if (

            developer_email_domain
            in href.lower()

            or

            dev_domain_clean
            in all_text

        ):

            score += 7

            reasons.append(
                "Developer email domain matches"
            )

    # --------------------------------------------------------
    # EMAIL USERNAME
    # --------------------------------------------------------

    for username, label in [

        (
            support_username,
            "Support email username"
        ),

        (
            developer_username,
            "Developer email username"
        )

    ]:

        if (

            username

            and

            len(username) >= 4

            and

            username in all_text

        ):

            score += 2

            reasons.append(
                label + " matches"
            )

    # --------------------------------------------------------
    # VALID LINKEDIN COMPANY
    # --------------------------------------------------------

    if (

        "linkedin.com/company/"
        in linkedin.lower()

    ):

        score += 2

        reasons.append(
            "Valid LinkedIn company URL"
        )

    # --------------------------------------------------------
    # DIRECT WEBSITE FIND
    # --------------------------------------------------------

    if found_via == "WEBSITE_DIRECT":

        score += 10

        reasons.append(
            "LinkedIn found directly on developer website"
        )

    return score, reasons


# ============================================================
# 22. FIND LINKEDIN CANDIDATES
# ============================================================

def find_candidates(

    developer1,

    developer2,

    website,

    support_email,

    developer_email

):

    candidates = {}

    website_domain = root_domain(
        website
    )

    # --------------------------------------------------------
    # QUERIES
    # --------------------------------------------------------

    queries = []

    # Website/domain FIRST

    if website_domain:

        queries.append((

            f'"{website_domain}" LinkedIn company',

            "WEBSITE_DOMAIN"

        ))

        queries.append((

            f'site:linkedin.com/company "{website_domain}"',

            "WEBSITE_DOMAIN_LINKEDIN"

        ))

    # Developer 1

    if developer1:

        queries.append((

            f'"{developer1}" LinkedIn company',

            "DEVELOPER_1"

        ))

        queries.append((

            f'site:linkedin.com/company "{developer1}"',

            "DEVELOPER_1_LINKEDIN"

        ))

    # Developer 2

    if developer2:

        queries.append((

            f'"{developer2}" LinkedIn company',

            "DEVELOPER_2"

        ))

        queries.append((

            f'site:linkedin.com/company "{developer2}"',

            "DEVELOPER_2_LINKEDIN"

        ))

    # Support email

    support_email = normalize_email(
        support_email
    )

    if support_email:

        support_domain = (
            email_domain(
                support_email
            )
        )

        if (

            support_domain

            and

            support_domain
            not in FREE_EMAIL_DOMAINS

        ):

            queries.append((

                f'"{support_domain}" LinkedIn company',

                "SUPPORT_EMAIL_DOMAIN"

            ))

    # Developer email

    developer_email = (
        normalize_email(
            developer_email
        )
    )

    if developer_email:

        developer_domain = (
            email_domain(
                developer_email
            )
        )

        if (

            developer_domain

            and

            developer_domain
            not in FREE_EMAIL_DOMAINS

        ):

            queries.append((

                f'"{developer_domain}" LinkedIn company',

                "DEVELOPER_EMAIL_DOMAIN"

            ))

    # --------------------------------------------------------
    # RUN QUERIES
    # --------------------------------------------------------

    for query, found_via in queries:

        print(
            f"      Search: {query}"
        )

        results = run_search(
            query,
            MAX_RESULTS
        )

        linkedin_results = (
            linkedin_from_search_results(
                results
            )
        )

        for item in linkedin_results:

            linkedin = item[
                "linkedin"
            ]

            score, reasons = (
                score_candidate(

                    linkedin=linkedin,

                    developer1=developer1,

                    developer2=developer2,

                    website=website,

                    support_email=support_email,

                    developer_email=developer_email,

                    title=item[
                        "title"
                    ],

                    href=item[
                        "href"
                    ],

                    body=item[
                        "body"
                    ],

                    found_via=found_via

                )
            )

            if linkedin not in candidates:

                candidates[
                    linkedin
                ] = {

                    "linkedin":
                        linkedin,

                    "score":
                        score,

                    "reasons":
                        reasons,

                    "found_via":
                        found_via,

                    "title":
                        item["title"],

                    "href":
                        item["href"],

                    "body":
                        item["body"],

                    "matched_signal":
                        query

                }

            else:

                candidates[
                    linkedin
                ][
                    "score"
                ] += score

                candidates[
                    linkedin
                ][
                    "reasons"
                ].extend(
                    reasons
                )

        # ----------------------------------------------------
        # EARLY STOP
        # ----------------------------------------------------

        best_score = max(

            [
                x["score"]
                for x in candidates.values()
            ],

            default=0

        )

        if best_score >= EARLY_STOP_SCORE:

            print(
                f"      Strong candidate found "
                f"(score {best_score})"
            )

            break

    return list(
        candidates.values()
    )


# ============================================================
# 23. FIND LINKEDIN COMPANY WEBSITE
# ============================================================

def find_linkedin_website(
    linkedin
):

    linkedin = clean_input(
        linkedin
    )

    if not linkedin:
        return ""

    if linkedin in LINKEDIN_WEBSITE_CACHE:

        return LINKEDIN_WEBSITE_CACHE[
            linkedin
        ]

    slug = linkedin_slug(
        linkedin
    )

    if not slug:

        LINKEDIN_WEBSITE_CACHE[
            linkedin
        ] = ""

        return ""

    queries = [

        f'"{linkedin}" website',

        f'"{slug}" company website',

        f'"{slug}" official website'

    ]

    found_website = ""

    for query in queries:

        results = run_search(
            query,
            5
        )

        for result in results:

            href = clean_input(
                result.get(
                    "href",
                    ""
                )
            )

            if not href:
                continue

            # Skip LinkedIn
            if (
                "linkedin.com"
                in href.lower()
            ):
                continue

            href_domain = root_domain(
                href
            )

            if not href_domain:
                continue

            # Skip social sites
            skip_domains = [

                "facebook.com",
                "instagram.com",
                "twitter.com",
                "x.com",
                "youtube.com",
                "tiktok.com",
                "crunchbase.com",
                "glassdoor.com",
                "indeed.com"

            ]

            if href_domain in skip_domains:
                continue

            if href.startswith(
                (
                    "http://",
                    "https://"
                )
            ):

                found_website = (
                    normalize_website(
                        href
                    )
                )

                if found_website:
                    break

        if found_website:
            break

    LINKEDIN_WEBSITE_CACHE[
        linkedin
    ] = found_website

    return found_website


# ============================================================
# 24. WEBSITE MATCH
# ============================================================

def website_match(

    original_website,

    linkedin_website

):

    original_domain = root_domain(
        original_website
    )

    linkedin_domain = root_domain(
        linkedin_website
    )

    if (

        not original_domain

        or

        not linkedin_domain

    ):

        return "NOT VERIFIED"

    if original_domain == linkedin_domain:

        return "YES"

    return "NO"


# ============================================================
# 25. CONFIDENCE
# ============================================================

def calculate_confidence(

    score,

    linkedin_website,

    match

):

    if (

        linkedin_website

        and

        match == "YES"

    ):

        return "VERY HIGH"

    if (

        linkedin_website

        and

        match == "NO"

    ):

        if score >= 15:
            return "HIGH - WEBSITE MISMATCH"

        elif score >= 8:
            return "MEDIUM - WEBSITE MISMATCH"

        else:
            return "LOW - WEBSITE MISMATCH"

    if score >= 20:
        return "HIGH"

    if score >= 12:
        return "MEDIUM"

    if score >= 6:
        return "LOW - TOP MATCH"

    return "VERY LOW - TOP MATCH"


# ============================================================
# 26. PROCESS ONE APP
# ============================================================

def process_app(

    bundle_id,

    developer1,

    developer2,

    website,

    support_email,

    developer_email

):

    bundle_id = clean_input(
        bundle_id
    )

    developer1 = clean_input(
        developer1
    )

    developer2 = clean_input(
        developer2
    )

    website = normalize_website(
        website
    )

    support_email = normalize_email(
        support_email
    )

    developer_email = normalize_email(
        developer_email
    )

    # --------------------------------------------------------
    # CACHE KEY
    # --------------------------------------------------------

    cache_key = (

        bundle_id.lower(),

        developer1.lower(),

        developer2.lower(),

        website.lower(),

        support_email.lower(),

        developer_email.lower()

    )

    if cache_key in COMPANY_RESULT_CACHE:

        cached = (
            COMPANY_RESULT_CACHE[
                cache_key
            ].copy()
        )

        cached["Cache"] = "HIT"

        return cached

    # --------------------------------------------------------
    # 1. WEBSITE DIRECT
    # --------------------------------------------------------

    direct_results = []

    if website:

        print(
            "   Checking developer website..."
        )

        direct_results = (
            search_website_directly(
                website
            )
        )

    # --------------------------------------------------------
    # DIRECT LINKEDIN
    # --------------------------------------------------------

    if direct_results:

        linkedin = (
            direct_results[0][
                "linkedin"
            ]
        )

        found_via = (
            "WEBSITE_DIRECT"
        )

        score, reasons = (
            score_candidate(

                linkedin=linkedin,

                developer1=developer1,

                developer2=developer2,

                website=website,

                support_email=support_email,

                developer_email=developer_email,

                title="",

                href=linkedin,

                body="",

                found_via="WEBSITE_DIRECT"

            )
        )

        score += 10

        if (
            "LinkedIn found directly on developer website"
            not in reasons
        ):

            reasons.append(
                "LinkedIn found directly on developer website"
            )

        matched_signal = website

    else:

        # ----------------------------------------------------
        # 2. SEARCH
        # ----------------------------------------------------

        print(
            "   Searching LinkedIn..."
        )

        candidates = find_candidates(

            developer1=developer1,

            developer2=developer2,

            website=website,

            support_email=support_email,

            developer_email=developer_email

        )

        if not candidates:

            result = {

                "Bundle ID":
                    bundle_id,

                "Developer 1":
                    developer1,

                "Developer 2":
                    developer2,

                "Website":
                    website,

                "LinkedIn":
                    "NOT FOUND",

                "LinkedIn Website":
                    "",

                "Website Match":
                    "NOT VERIFIED",

                "Found Via":
                    "",

                "Matched Signal":
                    "",

                "Score":
                    0,

                "Confidence":
                    "NOT FOUND",

                "Reasons":
                    "No LinkedIn company candidate found",

                "Cache":
                    "MISS"

            }

            COMPANY_RESULT_CACHE[
                cache_key
            ] = result.copy()

            return result

        # ----------------------------------------------------
        # ALWAYS TOP CANDIDATE
        # ----------------------------------------------------

        candidates = sorted(

            candidates,

            key=lambda x:
                x["score"],

            reverse=True

        )

        best = candidates[0]

        linkedin = best[
            "linkedin"
        ]

        score = best[
            "score"
        ]

        reasons = list(
            dict.fromkeys(
                best["reasons"]
            )
        )

        found_via = best[
            "found_via"
        ]

        matched_signal = best[
            "matched_signal"
        ]

    # --------------------------------------------------------
    # 3. LINKEDIN WEBSITE
    # --------------------------------------------------------

    print(
        "   Checking LinkedIn website..."
    )

    linkedin_website = (
        find_linkedin_website(
            linkedin
        )
    )

    # --------------------------------------------------------
    # 4. WEBSITE MATCH
    # --------------------------------------------------------

    match = website_match(

        website,

        linkedin_website

    )

    # --------------------------------------------------------
    # 5. CONFIDENCE
    # --------------------------------------------------------

    confidence = (
        calculate_confidence(

            score=score,

            linkedin_website=
                linkedin_website,

            match=match

        )
    )

    # --------------------------------------------------------
    # REASON
    # --------------------------------------------------------

    if match == "YES":

        reasons.append(
            "LinkedIn website matches original website"
        )

    elif match == "NO":

        reasons.append(
            "LinkedIn website does not match original website"
        )

    else:

        reasons.append(
            "LinkedIn website could not be independently verified"
        )

    reasons = list(
        dict.fromkeys(
            reasons
        )
    )

    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    result = {

        "Bundle ID":
            bundle_id,

        "Developer 1":
            developer1,

        "Developer 2":
            developer2,

        "Website":
            website,

        "LinkedIn":
            linkedin,

        "LinkedIn Website":
            linkedin_website,

        "Website Match":
            match,

        "Found Via":
            found_via,

        "Matched Signal":
            matched_signal,

        "Score":
            score,

        "Confidence":
            confidence,

        "Reasons":
            " | ".join(
                reasons
            ),

        "Cache":
            "MISS"

    }

    COMPANY_RESULT_CACHE[
        cache_key
    ] = result.copy()

    return result





# ----------------------------- GOOGLE PLAY ENRICHMENT -----------------------------

GOOGLE_PLAY_CACHE = {}
# Google Play country parameter uses ISO 3166-1 alpha-2 country codes.
# Start with common markets, then try the remaining ISO country codes.
GOOGLE_PLAY_PRIORITY_COUNTRIES = ["in", "us", "gb", "ca", "au", "sg", "id", "ph", "my", "th", "vn", "br", "mx", "de", "fr", "jp", "kr", "ae", "sa"]
GOOGLE_PLAY_ALL_COUNTRIES = "aw af ao ai ax al ad ae ar am as aq tf ag au at az bi be bj bq bf bd bg bh bs ba bl by bz bm bo br bb bn bt bv bw cf ca cc ch cl cn ci cm cd cg ck co km cv cr cu cw cx ky cy cz de dj dm dk do dz ec eg er eh es ee et fi fj fk fr fo fm ga gb ge gg gh gi gn gp gm gw gq gr gd gl gt gf gu gy hk hm hn hr ht hu id im in io ie ir iq is il it jm je jo jp kz ke kg kh ki kn kr kw la lb lr ly lc li lk ls lt lu lv mo mf ma mc md mg mv mx mh mk ml mt mm me mn mp mz mr ms mq mu mw my yt na nc ne nf ng ni nu nl no np nr nz om pk pa pn pe ph pw pg pl pr kp pt py ps pf qa re ro ru rw sa sd sn sg gs sh sj sb sl sv sm so pm rs ss st sr sk si se sz sx sc sy tc td tg th tj tk tm tl to tt tn tr tv tw tz ug ua um uy us uz va vc ve vg vi vn vu wf ws ye za zm zw".split()
GOOGLE_PLAY_COUNTRIES = GOOGLE_PLAY_PRIORITY_COUNTRIES + [
    code for code in GOOGLE_PLAY_ALL_COUNTRIES if code not in GOOGLE_PLAY_PRIORITY_COUNTRIES
]


def get_google_play_details(bundle_id):
    """Find an app in Google Play across supported country storefronts."""
    bundle_id = clean_input(bundle_id)
    if not bundle_id:
        return {}, "missing_bundle_id"

    cache_key = bundle_id.lower()
    if cache_key in GOOGLE_PLAY_CACHE:
        return GOOGLE_PLAY_CACHE[cache_key], "cache_hit"

    last_error = "not_found_in_checked_regions"
    checked = 0
    for country in GOOGLE_PLAY_COUNTRIES:
        checked += 1
        try:
            # google-play-scraper's app() does not accept a timeout argument.
            details = google_play_app(bundle_id, lang="en", country=country)
            if details:
                GOOGLE_PLAY_CACHE[cache_key] = details
                print(f"Google Play found {bundle_id} in storefront {country.upper()} after {checked} region checks")
                return details, f"found:{country}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            # Not-found/region-unavailable errors are expected during a global scan.
            # Print periodic diagnostics instead of flooding the Actions log.
            if checked == 1 or checked % 25 == 0:
                print(f"Google Play scan {checked}/{len(GOOGLE_PLAY_COUNTRIES)} for {bundle_id}; latest {country.upper()} response: {last_error}")

    status = f"not_found_after_{checked}_regions; last_error={last_error}"
    print(f"Google Play did not return {bundle_id} in {checked} checked country storefronts")
    return {}, status


def enrich_from_google_play(row):
    """Fill missing fields from Google Play while preserving supplied values."""
    bundle_id = clean_input(row.get("bundle_id"))
    details, lookup_status = get_google_play_details(bundle_id)
    enriched = {
        "developer_1": clean_input(row.get("developer_1")) or clean_input(details.get("developer")),
        # Google Play does not provide a second developer entity; never infer one.
        "developer_2": clean_input(row.get("developer_2")),
        "website": clean_input(row.get("website")) or clean_input(details.get("developerWebsite")),
        "support_email": clean_input(row.get("support_email")) or clean_input(details.get("developerEmail")),
        "developer_email": clean_input(row.get("developer_email")) or clean_input(details.get("developerEmail")),
    }
    metadata = {
        "google_play_app_name": clean_input(details.get("title")),
        "google_play_developer": clean_input(details.get("developer")),
        "google_play_developer_id": clean_input(details.get("developerId")),
        "google_play_developer_website": clean_input(details.get("developerWebsite")),
        "google_play_developer_email": clean_input(details.get("developerEmail")),
        "google_play_lookup_status": lookup_status,
        "google_play_url": f"https://play.google.com/store/apps/details?id={urllib.parse.quote(bundle_id)}",
    }
    return enriched, metadata


# ----------------------------- CLOUD CONFIG -----------------------------

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


def save_google_play_enrichment(app_id, original_input_data, metadata, enriched):
    merged = dict(original_input_data) if isinstance(original_input_data, dict) else {}
    merged.update(metadata)
    payload = {
        "input_data": merged,
        "developer_1": enriched.get("developer_1", ""),
        "developer_2": enriched.get("developer_2", ""),
        "website": enriched.get("website", ""),
        "support_email": enriched.get("support_email", ""),
        "developer_email": enriched.get("developer_email", ""),
    }
    db_request("PATCH", "research_apps", params={"id": f"eq.{app_id}"}, payload=payload)


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
            try:
                enriched, google_play_metadata = enrich_from_google_play(row)
                developer1 = enriched["developer_1"]
                developer2 = enriched["developer_2"]
                website = enriched["website"]
                support_email = enriched["support_email"]
                developer_email = enriched["developer_email"]
                save_google_play_enrichment(app_id, row.get("input_data"), google_play_metadata, enriched)
                print(f"   Google Play: {google_play_metadata.get('google_play_lookup_status')} | App: {google_play_metadata.get('google_play_app_name')}")
            except Exception as enrichment_exc:
                print(f"Google Play enrichment error for {bundle_id}: {enrichment_exc}")
                developer1 = clean_input(row.get("developer_1"))
                developer2 = clean_input(row.get("developer_2"))
                website = clean_input(row.get("website"))
                support_email = clean_input(row.get("support_email"))
                developer_email = clean_input(row.get("developer_email"))

            print(f"\\n[{processed_this_run + 1}] Processing {bundle_id}")
            print(f"   Developer 1: {developer1}")
            print(f"   Developer 2: {developer2}")
            print(f"   Website: {website}")
            print(f"   Support Email: {support_email}")
            print(f"   Developer Email: {developer_email}")

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
                print(f"   LinkedIn: {result.get('LinkedIn', '')}")
                print(f"   Found Via: {result.get('Found Via', '')}")
                print(f"   Score: {result.get('Score', 0)}")
                print(f"   Confidence: {result.get('Confidence', '')}")
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
                save_app_result(
                    app_id,
                    error_result,
                    status="error",
                    error_message=error_text,
                )

            processed_this_run += 1
            update_job(processed_rows=processed_this_run)

            if processed_this_run:
                time.sleep(random.uniform(MIN_APP_DELAY, MAX_APP_DELAY))

    all_rows = db_request(
        "GET",
        "research_apps",
        params={
            "select": "id,status",
            "job_id": f"eq.{JOB_ID}",
            "limit": "10000",
        },
    ) or []
    finished = sum(
        1 for item in all_rows
        if item.get("status") in ("completed", "error")
    )
    total = len(all_rows)
    final_status = "completed" if finished >= total else "failed"
    update_job(status=final_status, processed_rows=finished)
    print(f"Worker finished. {finished}/{total} rows completed or errored.")


if __name__ == "__main__":
    main()
