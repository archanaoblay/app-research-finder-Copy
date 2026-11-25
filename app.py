

import streamlit as st
import pandas as pd
import requests
import uuid
import io
import time
from datetime import datetime, timezone

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

if not all([
    SUPABASE_URL,
    SUPABASE_KEY,
    GITHUB_TOKEN,
    GITHUB_OWNER,
    GITHUB_REPO
]):
    st.warning(
        "App configuration is incomplete. "
        "We'll add the required cloud secrets after deploying the app."
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
        method,
        url,
        headers=headers,
        params=params,
        json=payload,
        timeout=60
    )

    if not response.ok:
        raise Exception(
            f"Supabase error ({response.status_code}): "
            f"{response.text[:1000]}"
        )

    if response.text:
        return response.json()

    return []


def trigger_github_workflow(job_id):
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/actions/workflows/"
        f"{GITHUB_WORKFLOW}/dispatches"
    )

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    payload = {
        "ref": GITHUB_BRANCH,
        "inputs": {
            "job_id": job_id
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code != 204:
        raise Exception(
            f"GitHub workflow error ({response.status_code}): "
            f"{response.text[:1000]}"
        )


# ---------------- FILE PROCESSING ----------------

def read_uploaded_file(uploaded_file):
    file_name = uploaded_file.name.lower()
    file_bytes = uploaded_file.getvalue()

    if file_name.endswith(".csv"):
        try:
            return pd.read_csv(io.BytesIO(file_bytes))
        except UnicodeDecodeError:
            return pd.read_csv(
                io.BytesIO(file_bytes),
                encoding="latin-1"
            )

    if file_name.endswith((".xlsx", ".xls")):
        return pd.read_excel(io.BytesIO(file_bytes))

    raise ValueError("Please upload a CSV or Excel file.")


def normalize_column(value):
    return str(value).strip().lower().replace("_", " ").replace("-", " ")


def find_column(columns, aliases):
    normalized = {
        normalize_column(col): col
        for col in columns
    }

    for alias in aliases:
        key = normalize_column(alias)
        if key in normalized:
            return normalized[key]

    return None


def detect_columns(df):
    columns = list(df.columns)

    bundle_col = find_column(
        columns,
        [
            "Bundle ID",
            "BundleID",
            "bundle_id",
            "Package Name",
            "Package"
        ]
    )

    website_col = find_column(
        columns,
        [
            "Website",
            "Developer Website",
            "App Support Website",
            "Support Website"
        ]
    )

    support_email_col = find_column(
        columns,
        ["Support Email", "App Support Email"]
    )

    developer_email_col = find_column(
        columns,
        ["Developer Email"]
    )

    developer_cols = [
        col for col in columns
        if normalize_column(col).startswith("developer")
        and col not in [
            website_col,
            support_email_col,
            developer_email_col
        ]
    ]

    developer_1_col = developer_cols[0] if len(developer_cols) > 0 else None
    developer_2_col = developer_cols[1] if len(developer_cols) > 1 else None

    return {
        "bundle": bundle_col,
        "developer_1": developer_1_col,
        "developer_2": developer_2_col,
        "website": website_col,
        "support_email": support_email_col,
        "developer_email": developer_email_col
    }


def clean_value(value):
    if pd.isna(value):
        return None

    value = str(value).strip()

    if not value or value.lower() in ["nan", "none", "null"]:
        return None

    return value


# ---------------- CREATE RESEARCH JOB ----------------

def create_research_job(df, file_name, columns):
    bundle_col = columns["bundle"]

    if not bundle_col:
        raise ValueError(
            "Bundle ID column not detected. "
            "Please include a column named Bundle ID, BundleID, "
            "Package Name, or Package."
        )

    job_id = str(uuid.uuid4())

    app_records = []

    for _, row in df.iterrows():
        bundle_id = clean_value(row.get(bundle_col))

        if not bundle_id:
            continue

        input_data = {
            str(col): clean_value(row[col])
            for col in df.columns
        }

        app_records.append({
            "id": str(uuid.uuid4()),
            "job_id": job_id,
            "bundle_id": bundle_id,
            "developer_1": clean_value(
                row.get(columns["developer_1"])
            ) if columns["developer_1"] else None,
            "developer_2": clean_value(
                row.get(columns["developer_2"])
            ) if columns["developer_2"] else None,
            "website": clean_value(
                row.get(columns["website"])
            ) if columns["website"] else None,
            "support_email": clean_value(
                row.get(columns["support_email"])
            ) if columns["support_email"] else None,
            "developer_email": clean_value(
                row.get(columns["developer_email"])
            ) if columns["developer_email"] else None,
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

    supabase_request(
        "POST",
        "research_jobs",
        payload=job_record
    )

    # Insert app records in batches
    for start in range(0, len(app_records), 100):
        batch = app_records[start:start + 100]

        supabase_request(
            "POST",
            "research_apps",
            payload=batch
        )

    # Trigger the cloud worker only after all rows are inserted
    trigger_github_workflow(job_id)

    return job_id, len(app_records)


# ---------------- DASHBOARD ----------------

if not SUPABASE_URL or not SUPABASE_KEY:
    st.info(
        "The dashboard code is ready. Cloud secrets will be configured "
        "after deployment."
    )
    st.stop()

tab1, tab2 = st.tabs(["New Research", "Research History"])

# ---------------- NEW RESEARCH ----------------

with tab1:
    st.subheader("Upload your app list")

    uploaded_file = st.file_uploader(
        "Upload CSV or Excel file",
        type=["csv", "xlsx", "xls"]
    )

    if uploaded_file:
        try:
            df = read_uploaded_file(uploaded_file)
            columns = detect_columns(df)

            st.success(
                f"File loaded successfully: {len(df):,} rows"
            )

            st.write("Preview")
            st.dataframe(df.head(10), use_container_width=True)

            st.write("Detected columns")

            detected = {
                "Bundle ID": columns["bundle"],
                "Developer 1": columns["developer_1"],
                "Developer 2": columns["developer_2"],
                "Website": columns["website"],
                "Support Email": columns["support_email"],
                "Developer Email": columns["developer_email"]
            }

            st.json(detected)

            if not columns["bundle"]:
                st.error("A Bundle ID column is required.")

            if st.button(
                "Start App Research",
                type="primary",
                disabled=not columns["bundle"],
                use_container_width=True
            ):
                if not all([
                    GITHUB_TOKEN,
                    GITHUB_OWNER,
                    GITHUB_REPO
                ]):
                    st.error(
                        "GitHub configuration is missing. "
                        "We'll configure it after deployment."
                    )
                else:
                    with st.spinner(
                        "Creating your research job and starting the cloud worker..."
                    ):
                        try:
                            job_id, total = create_research_job(
                                df,
                                uploaded_file.name,
                                columns
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
            "GET",
            "research_jobs",
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
                    "id",
                    "file_name",
                    "status",
                    "total_rows",
                    "processed_rows",
                    "created_at"
                ]
                if col in jobs_df.columns
            ]

            st.dataframe(
                jobs_df[display_columns],
                use_container_width=True,
                hide_index=True
            )

            job_options = {
                f"{job.get('file_name') or 'Research job'} | "
                f"{job.get('id')}": job.get("id")
                for job in jobs
            }

            selected_label = st.selectbox(
                "Select a job to view results",
                list(job_options.keys())
            )

            selected_job_id = job_options[selected_label]

            selected_job = next(
                job for job in jobs
                if job.get("id") == selected_job_id
            )

            total = selected_job.get("total_rows", 0) or 0
            processed = selected_job.get("processed_rows", 0) or 0

            st.write(
                f"Progress: {processed:,} / {total:,} apps"
            )

            if total:
                st.progress(min(processed / total, 1.0))

            results = supabase_request(
                "GET",
                "research_apps",
                params={
                    "select": "*",
                    "job_id": f"eq.{selected_job_id}",
                    "order": "created_at.asc",
                    "limit": "10000"
                }
            )

            if results:
                result_rows = []

                for item in results:
                    result_rows.append({
                        "Bundle ID": item.get("bundle_id"),
                        "Developer 1": item.get("developer_1"),
                        "Developer 2": item.get("developer_2"),
                        "Website": item.get("website"),
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

                results_df = pd.DataFrame(result_rows)

                st.dataframe(
                    results_df,
                    use_container_width=True,
                    hide_index=True
                )

                excel_buffer = io.BytesIO()

                with pd.ExcelWriter(
                    excel_buffer,
                    engine="openpyxl"
                ) as writer:
                    results_df.to_excel(
                        writer,
                        index=False,
                        sheet_name="Research Results"
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
    "App Research Finder | Cloud processing powered by GitHub Actions "
    "and Supabase"
)
