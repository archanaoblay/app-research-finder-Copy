import streamlit as st
import pandas as pd
import requests
import uuid
import io
import re

st.set_page_config(
    page_title="App Research Finder",
    page_icon="🔎",
    layout="wide"
)

st.title("App Research Finder")
st.caption("Cloud-based app and developer research dashboard")

# ---------------- CONFIGURATION ----------------

def get_config(name):
    try:
        return st.secrets[name]
    except Exception:
        return None

SUPABASE_URL = get_config("SUPABASE_URL")
SUPABASE_KEY = get_config("SUPABASE_SERVICE_ROLE_KEY")
GITHUB_TOKEN = get_config("GITHUB_TOKEN")
GITHUB_OWNER = get_config("GITHUB_OWNER")
GITHUB_REPO = get_config("GITHUB_REPO")
GITHUB_WORKFLOW = "process.yml"
GITHUB_BRANCH = "main"

if not all([SUPABASE_URL, SUPABASE_KEY, GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO]):
    st.warning(
        "App configuration is incomplete. Check the Streamlit secrets "
        "and GitHub repository settings."
    )

# ---------------- SUPABASE HELPERS ----------------

def supabase_request(method, table, params=None, payload=None):
    url = f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    response = requests.request(
        method, url, headers=headers, params=params, json=payload, timeout=60
    )
    if not response.ok:
        raise Exception(
            f"Supabase error ({response.status_code}): {response.text[:1000]}"
        )
    return response.json() if response.text else []


def trigger_github_workflow(job_id):
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/"
        f"actions/workflows/{GITHUB_WORKFLOW}/dispatches"
    )
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    response = requests.post(
        url,
        headers=headers,
        json={"ref": GITHUB_BRANCH, "inputs": {"job_id": job_id}},
        timeout=30
    )
    if response.status_code != 204:
        raise Exception(
            f"GitHub workflow error ({response.status_code}): {response.text[:1000]}"
        )


# ---------------- FILE PROCESSING ----------------

def read_uploaded_file(uploaded_file):
    file_name = uploaded_file.name.lower()
    file_bytes = uploaded_file.getvalue()
    if file_name.endswith(".csv"):
        try:
            return pd.read_csv(io.BytesIO(file_bytes))
        except UnicodeDecodeError:
            return pd.read_csv(io.BytesIO(file_bytes), encoding="latin-1")
    if file_name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(file_bytes))
    raise ValueError("Please upload a CSV or Excel file.")


def normalize_column(value):
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("_", " ").replace("-", " "))


def normalized_lookup(columns):
    return {normalize_column(col): col for col in columns}


def find_column(columns, aliases):
    lookup = normalized_lookup(columns)
    for alias in aliases:
        found = lookup.get(normalize_column(alias))
        if found is not None:
            return found
    return None


def detect_columns(df):
    columns = list(df.columns)

    bundle_col = find_column(columns, [
        "Bundle ID", "BundleID", "bundle_id", "Package Name", "Package", "App ID"
    ])
    app_name_col = find_column(columns, [
        "App Name", "Application Name", "App", "Title", "Application"
    ])
    website_col = find_column(columns, [
        "Website", "Developer Website", "Company Website",
        "App Support Website", "Support Website", "URL"
    ])
    support_email_col = find_column(columns, [
        "Support Email", "App Support Email", "Customer Support Email"
    ])
    developer_email_col = find_column(columns, [
        "Developer Email", "Business Email", "Contact Email"
    ])

    developer_1_col = find_column(columns, [
        "Developer 1", "Developer", "Developer Name", "Primary Developer"
    ])
    developer_2_col = find_column(columns, [
        "Developer 2", "Publisher Company", "Parent Company",
        "Publisher", "Secondary Developer"
    ])

    # Backward-compatible detection for columns named Developer 1 / Developer 2,
    # including files with non-standard column names.
    if developer_1_col is None:
        candidates = [
            col for col in columns
            if normalize_column(col).startswith("developer")
            and col not in {website_col, support_email_col, developer_email_col}
        ]
        if candidates:
            developer_1_col = candidates[0]
            if developer_2_col is None and len(candidates) > 1:
                developer_2_col = candidates[1]

    return {
        "bundle": bundle_col,
        "app_name": app_name_col,
        "developer_1": developer_1_col,
        "developer_2": developer_2_col,
        "website": website_col,
        "support_email": support_email_col,
        "developer_email": developer_email_col
    }


def clean_value(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    value = str(value).strip()
    if not value or value.lower() in ["nan", "none", "null", "n/a", "na", "-"]:
        return None
    return value


# ---------------- CREATE RESEARCH JOB ----------------

def create_research_job(df, file_name, columns):
    bundle_col = columns["bundle"]
    if not bundle_col:
        raise ValueError(
            "Bundle ID column not detected. Include a column named Bundle ID, "
            "BundleID, Package Name, Package, or App ID."
        )

    job_id = str(uuid.uuid4())
    app_records = []

    for _, row in df.iterrows():
        bundle_id = clean_value(row.get(bundle_col))
        if not bundle_id:
            continue

        # Preserve every supplied input column so the worker can use additional
        # fields (including aliases not explicitly mapped by this dashboard).
        input_data = {str(col): clean_value(row[col]) for col in df.columns}

        def field_value(field):
            col = columns.get(field)
            return clean_value(row.get(col)) if col else None

        app_records.append({
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "bundle_id": bundle_id,
            "developer_1": field_value("developer_1"),
            "developer_2": field_value("developer_2"),
            "website": field_value("website"),
            "support_email": field_value("support_email"),
            "developer_email": field_value("developer_email"),
            "input_data": input_data,
            "status": "pending"
        })

    if not app_records:
        raise ValueError("No valid Bundle IDs found in the uploaded file.")

    job_record = {
        "id": job_id,
        "file_name": file_name,
        "status": "queued",
        "total_rows": len(app_records),
        "processed_rows": 0
    }

    supabase_request("POST", "research_jobs", payload=job_record)

    for start in range(0, len(app_records), 100):
        supabase_request(
            "POST", "research_apps", payload=app_records[start:start + 100]
        )

    trigger_github_workflow(job_id)
    return job_id, len(app_records)


# ---------------- RESULTS HELPERS ----------------

def input_value(input_data, *keys):
    if not isinstance(input_data, dict):
        return None
    lookup = {
        re.sub(r"[^a-z0-9]", "", str(k).lower()): v
        for k, v in input_data.items()
    }
    for key in keys:
        value = clean_value(lookup.get(re.sub(r"[^a-z0-9]", "", key.lower())))
        if value:
            return value
    return None


def build_result_rows(results):
    rows = []
    for item in results:
        data = item.get("input_data") or {}
        sources = data.get("enrichment_field_sources") or {}

        rows.append({
            "Bundle ID": item.get("bundle_id"),
            "App Name": data.get("app_name") or input_value(
                data, "App Name", "Application Name", "Title"
            ),
            "Developer 1": item.get("developer_1"),
            "Developer 2": item.get("developer_2"),
            "Website": item.get("website"),
            "Support Email": item.get("support_email"),
            "Developer Email": item.get("developer_email"),
            "App Name Source": sources.get("app_name"),
            "Developer 1 Source": sources.get("developer_1"),
            "Developer 2 Source": sources.get("developer_2"),
            "Website Source": sources.get("website"),
            "Support Email Source": sources.get("support_email"),
            "Developer Email Source": sources.get("developer_email"),
            "Google Play Lookup": data.get("google_play_lookup_status"),
            "Google Play URL": data.get("google_play_url"),
            "LinkedIn": item.get("linkedin"),
            "LinkedIn Website": item.get("linkedin_website"),
            "Website Match": item.get("website_match"),
            "Found Via": item.get("found_via"),
            "Matched Signal": item.get("matched_signal"),
            "Score": item.get("score"),
            "Confidence": item.get("confidence"),
            "Reasons": item.get("reasons"),
            "Cache": item.get("cache_status"),
            "Status": item.get("status"),
            "Error": item.get("error_message")
        })
    return rows


# ---------------- DASHBOARD ----------------

if not SUPABASE_URL or not SUPABASE_KEY:
    st.info("Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in Streamlit secrets.")
    st.stop()

tab1, tab2 = st.tabs(["New Research", "Research History"])

# ---------------- NEW RESEARCH ----------------

with tab1:
    st.subheader("Upload your app list")
    st.caption(
        "Bundle ID is required. App Name, Developer 1, Developer 2 / Publisher "
        "Company, Website, Support Email and Developer Email are optional."
    )

    uploaded_file = st.file_uploader(
        "Upload CSV or Excel file", type=["csv", "xlsx", "xls"]
    )

    if uploaded_file:
        try:
            df = read_uploaded_file(uploaded_file)
            columns = detect_columns(df)

            st.success(f"File loaded successfully: {len(df):,} rows")
            st.write("Preview")
            st.dataframe(df.head(10), use_container_width=True)

            detected = {
                "Bundle ID": columns["bundle"],
                "App Name": columns["app_name"],
                "Developer 1": columns["developer_1"],
                "Developer 2 / Publisher Company": columns["developer_2"],
                "Website": columns["website"],
                "Support Email": columns["support_email"],
                "Developer Email": columns["developer_email"]
            }
            st.write("Detected columns")
            st.json(detected)

            if not columns["bundle"]:
                st.error("A Bundle ID column is required.")

            if st.button(
                "Start App Research",
                type="primary",
                disabled=not columns["bundle"],
                use_container_width=True
            ):
                if not all([GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO]):
                    st.error("GitHub configuration is missing in Streamlit secrets.")
                else:
                    with st.spinner(
                        "Creating your research job and starting the cloud worker..."
                    ):
                        try:
                            job_id, total = create_research_job(
                                df, uploaded_file.name, columns
                            )
                            st.session_state["latest_job_id"] = job_id
                            st.success(
                                f"Research started successfully for {total:,} apps."
                            )
                            st.code(job_id)
                        except Exception as error:
                            st.error(str(error))

        except Exception as error:
            st.error(f"Could not read the uploaded file: {error}")


# ---------------- RESEARCH HISTORY ----------------

with tab2:
    st.subheader("Research jobs")

    if st.button("Refresh jobs"):
        st.rerun()

    try:
        jobs = supabase_request(
            "GET", "research_jobs",
            params={
                "select": "*",
                "order": "created_at.desc",
                "limit": "50"
            }
        )

        if jobs:
            jobs_df = pd.DataFrame(jobs)
            display_columns = [
                col for col in [
                    "id", "file_name", "status", "total_rows",
                    "processed_rows", "created_at"
                ] if col in jobs_df.columns
            ]
            st.dataframe(
                jobs_df[display_columns],
                use_container_width=True,
                hide_index=True
            )

            job_options = {
                f"{job.get('file_name') or 'Research job'} | {job.get('id')}": job.get("id")
                for job in jobs
            }
            selected_label = st.selectbox(
                "Select a job to view results", list(job_options.keys())
            )
            selected_job_id = job_options[selected_label]
            selected_job = next(
                job for job in jobs if job.get("id") == selected_job_id
            )

            total = selected_job.get("total_rows", 0) or 0
            processed = selected_job.get("processed_rows", 0) or 0
            st.write(f"Progress: {processed:,} / {total:,} apps")
            if total:
                st.progress(min(processed / total, 1.0))

            results = supabase_request(
                "GET", "research_apps",
                params={
                    "select": "*",
                    "job_id": f"eq.{selected_job_id}",
                    "order": "created_at.asc",
                    "limit": "10000"
                }
            )

            if results:
                results_df = pd.DataFrame(build_result_rows(results))
                st.dataframe(
                    results_df,
                    use_container_width=True,
                    hide_index=True
                )

                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
                    results_df.to_excel(
                        writer, index=False, sheet_name="Research Results"
                    )

                st.download_button(
                    "Download Excel Results",
                    data=excel_buffer.getvalue(),
                    file_name=f"app_research_{selected_job_id[:8]}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
            else:
                st.info("No app results are available yet.")
        else:
            st.info("No research jobs found yet.")

    except Exception as error:
        st.error(f"Could not load research history: {error}")

st.divider()
st.caption(
    "App Research Finder | Cloud processing powered by GitHub Actions and Supabase"
)
