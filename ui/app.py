"""
VibeForge UI — Streamlit App
=============================
Two pages:
  🧬 Spec Sheet      — Option graph form + live impact panel + Draft Compile
  🔍 Review Workspace — Spec diff cards + Lock & Generate button + progress

Auth: fetches a dev token from Keycloak (password grant) and caches it for 5 min.
      All API calls go container-to-container: http://api:8000
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import streamlit as st

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
API_BASE = os.getenv("API_BASE_URL", "http://api:8000")
KC_BASE  = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KC_REALM = os.getenv("KEYCLOAK_REALM", "vibeforge")
DEV_USER = os.getenv("DEV_USERNAME", "admin@vibeforge.local")
DEV_PASS = os.getenv("DEV_PASSWORD", "admin123")
KC_CLIENT_ID     = os.getenv("KC_CLIENT_ID", "vibeforge-api")
KC_CLIENT_SECRET = os.getenv("KC_CLIENT_SECRET", "vibeforge-api-secret-change-me")

# ──────────────────────────────────────────────────────────────────────────────
# Page config + custom CSS
# ──────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="VibeForge",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
/* ── Global ── */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

/* ── Sidebar ── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f0f1a 0%, #1a1a2e 100%);
    border-right: 1px solid #2d2d4e;
}
[data-testid="stSidebar"] * { color: #e0e0f0 !important; }

/* ── Main area ── */
.stApp { background: #0d0d1f; color: #e0e0f0; }

/* ── Cards ── */
.vf-card {
    background: #1a1a2e;
    border: 1px solid #2d2d4e;
    border-radius: 12px;
    padding: 1.25rem 1.5rem;
    margin-bottom: 1rem;
}
.vf-card-title {
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: #7878aa;
    margin-bottom: 0.75rem;
}
.vf-card-highlight {
    border-left: 3px solid #7c3aed;
}

/* ── Impact pill ── */
.vf-pill {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.72rem;
    font-weight: 500;
    margin: 2px 3px 2px 0;
}
.vf-pill-purple { background: #4c1d95; color: #c4b5fd; }
.vf-pill-red    { background: #7f1d1d; color: #fca5a5; }
.vf-pill-green  { background: #14532d; color: #86efac; }
.vf-pill-blue   { background: #1e3a5f; color: #93c5fd; }
.vf-pill-yellow { background: #713f12; color: #fde68a; }

/* ── Hash display ── */
.vf-hash {
    font-family: 'Courier New', monospace;
    font-size: 0.75rem;
    color: #7c3aed;
    background: #1e1b4b;
    padding: 4px 10px;
    border-radius: 6px;
    letter-spacing: 0.05em;
}

/* ── Progress bar ── */
.vf-progress-label {
    font-size: 0.8rem;
    color: #a0a0c0;
    margin-top: 4px;
}

/* ── Buttons ── */
div.stButton > button {
    background: linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%);
    color: white;
    border: none;
    border-radius: 8px;
    padding: 0.5rem 1.5rem;
    font-weight: 600;
    font-size: 0.9rem;
    transition: opacity 0.2s;
    width: 100%;
}
div.stButton > button:hover { opacity: 0.85; }

/* ── Section header ── */
.vf-section-header {
    font-size: 1.5rem;
    font-weight: 700;
    color: #e0e0f0;
    margin-bottom: 0.25rem;
}
.vf-section-sub {
    font-size: 0.85rem;
    color: #7878aa;
    margin-bottom: 1.5rem;
}

/* ── Spec field row ── */
.vf-field-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid #2d2d4e;
    font-size: 0.85rem;
}
.vf-field-key   { color: #9090b0; }
.vf-field-value { color: #e0e0f0; font-weight: 500; }
.vf-field-changed { color: #c4b5fd; font-weight: 600; }

/* ── AC list ── */
.vf-ac-item {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 5px 0;
    font-size: 0.82rem;
    color: #c0c0e0;
}
.vf-ac-id { color: #7c3aed; font-weight: 600; min-width: 52px; }
</style>
""", unsafe_allow_html=True)


# ──────────────────────────────────────────────────────────────────────────────
# Auth helpers
# ──────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=270)   # 4.5 min — Keycloak tokens live 5 min by default
def _fetch_token() -> str | None:
    """Password grant — dev only. Returns access_token or None."""
    try:
        resp = httpx.post(
            f"{KC_BASE}/realms/{KC_REALM}/protocol/openid-connect/token",
            data={
                "client_id":     KC_CLIENT_ID,
                "client_secret": KC_CLIENT_SECRET,
                "username":      DEV_USER,
                "password":      DEV_PASS,
                "grant_type":    "password",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        return resp.json()["access_token"]
    except Exception as exc:
        st.error(f"Keycloak auth failed: {exc}")
        return None


def _headers() -> dict[str, str]:
    token = _fetch_token()
    if not token:
        st.stop()
    return {"Authorization": f"Bearer {token}"}


# ──────────────────────────────────────────────────────────────────────────────
# API helpers
# ──────────────────────────────────────────────────────────────────────────────
def api_get(path: str, params: dict | None = None) -> Any:
    try:
        r = httpx.get(f"{API_BASE}{path}", headers=_headers(), params=params, timeout=10.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text[:200]}")
        return None
    except Exception as e:
        st.error(f"API connection error: {e}")
        return None


def api_post(path: str, body: dict, auth: bool = True) -> Any:
    try:
        hdrs = _headers() if auth else {}
        r = httpx.post(f"{API_BASE}{path}", json=body, headers=hdrs, timeout=30.0)
        r.raise_for_status()
        return r.json()
    except httpx.HTTPStatusError as e:
        st.error(f"API error {e.response.status_code}: {e.response.text[:400]}")
        return None
    except Exception as e:
        st.error(f"API connection error: {e}")
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Default values for the option graph (used for impact diff)
# ──────────────────────────────────────────────────────────────────────────────
OPTION_DEFAULTS = {
    "auth_strategy":  "jwt",
    "db_tier":        "postgres",
    "ui_framework":   "react",
    "api_style":      "rest",
    "deploy_target":  "docker",
    "async_jobs":     "yes",
    "compliance_tier": "none",
    "search_enabled": "no",
}

# Human-readable labels for each option key
OPTION_LABELS = {
    "auth_strategy":  "Auth Strategy",
    "db_tier":        "Database Tier",
    "ui_framework":   "UI Framework",
    "api_style":      "API Style",
    "deploy_target":  "Deploy Target",
    "async_jobs":     "Async Jobs",
    "compliance_tier": "Compliance Tier",
    "search_enabled": "Search Enabled",
}


# ──────────────────────────────────────────────────────────────────────────────
# Computed impact (local — no API call needed)
# ──────────────────────────────────────────────────────────────────────────────
def compute_impact(sel: dict) -> dict:
    """
    Derive secondary fields from selections. Mirrors compiler.py logic.
    Kept in sync manually — Phase 2 will call /preview-spec instead.
    """
    comp = sel.get("compliance_tier", "none")

    pii: list[str] = []
    mfa = False
    data_class = "internal"

    if comp in ("gdpr", "hipaa"):
        pii = ["email", "name", "date_of_birth", "address"]
        data_class = "confidential"
    if comp == "hipaa":
        pii += ["diagnosis", "medication", "health_record_id"]
        mfa = True
        data_class = "restricted"
    if comp == "pci":
        pii = ["card_number", "cvv", "billing_address"]
        mfa = True
        data_class = "restricted"

    # Count acceptance criteria (mirrors compiler._build_acceptance logic)
    ac_count = 3   # base: health, auth, crud
    if sel.get("async_jobs") == "yes":
        ac_count += 1
    if sel.get("search_enabled") == "yes":
        ac_count += 1
    if comp in ("gdpr", "hipaa", "pci"):
        ac_count += 2

    # Infra notes
    infra_notes: list[str] = []
    if sel.get("deploy_target") == "k8s":
        infra_notes.append("Helm chart included")
        infra_notes.append("HPA configured")
    if sel.get("deploy_target") == "serverless":
        infra_notes.append("No persistent connections")
    if sel.get("db_tier") == "postgres":
        infra_notes.append("Alembic migrations")
    if sel.get("async_jobs") == "yes":
        infra_notes.append("Arq worker + Redis")
    if sel.get("search_enabled") == "yes":
        infra_notes.append("Qdrant vector store")

    return {
        "pii_fields":       pii,
        "mfa_required":     mfa,
        "data_class":       data_class,
        "ac_count":         ac_count,
        "infra_notes":      infra_notes,
        "compliance":       comp,
    }


# ──────────────────────────────────────────────────────────────────────────────
# HTML card helpers
# ──────────────────────────────────────────────────────────────────────────────
def pill(text: str, colour: str = "purple") -> str:
    return f'<span class="vf-pill vf-pill-{colour}">{text}</span>'


def field_row(key: str, value: str, changed: bool = False) -> str:
    val_class = "vf-field-changed" if changed else "vf-field-value"
    return (
        f'<div class="vf-field-row">'
        f'<span class="vf-field-key">{key}</span>'
        f'<span class="{val_class}">{value}</span>'
        f'</div>'
    )


def spec_card(title: str, rows: list[str], highlight: bool = False) -> None:
    cls = "vf-card vf-card-highlight" if highlight else "vf-card"
    inner = "".join(rows)
    st.markdown(
        f'<div class="{cls}"><div class="vf-card-title">{title}</div>{inner}</div>',
        unsafe_allow_html=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Page 1: 🧬 Spec Sheet
# ──────────────────────────────────────────────────────────────────────────────
def page_spec_sheet() -> None:
    st.markdown('<div class="vf-section-header">🧬 Spec Sheet</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="vf-section-sub">Choose your stack options. '
        'The Impact Panel updates live as you select.</div>',
        unsafe_allow_html=True,
    )

    # ── Project selector ─────────────────────────────────────────────────────
    projects_data = api_get("/api/v1/projects/")
    if not projects_data:
        st.info("No projects yet. Create one below.")
        projects_data = {"items": []}

    projects = projects_data.get("items", []) if isinstance(projects_data, dict) else projects_data
    proj_names = [p["name"] for p in projects] if projects else []
    proj_ids   = [p["id"]   for p in projects] if projects else []

    col_proj, col_new = st.columns([3, 1])
    with col_proj:
        selected_proj_name = st.selectbox(
            "Project", proj_names if proj_names else ["— no projects —"], key="proj_selector"
        )
    with col_new:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("+ New Project", key="new_proj_btn"):
            st.session_state["creating_project"] = True

    # Create project dialog
    if st.session_state.get("creating_project"):
        with st.form("new_project_form"):
            new_name = st.text_input("Project name", placeholder="My App")
            new_desc = st.text_input("Description (optional)")
            submitted = st.form_submit_button("Create")
            if submitted and new_name:
                result = api_post(
                    "/api/v1/projects/",
                    {"name": new_name, "description": new_desc},
                )
                if result:
                    st.success(f"Created project: {result['id']}")
                    st.session_state["creating_project"] = False
                    st.cache_data.clear()
                    st.rerun()

    # Get selected project id
    project_id: str | None = None
    if proj_ids and selected_proj_name in proj_names:
        project_id = proj_ids[proj_names.index(selected_proj_name)]

    st.divider()

    # ── Load option graph ─────────────────────────────────────────────────────
    @st.cache_data(ttl=3600)
    def load_option_graph():
        return api_get("/api/v1/projects/option-graph")

    og = load_option_graph() or {}

    # ── Layout: form (left) + impact panel (right) ────────────────────────────
    form_col, impact_col = st.columns([2, 1], gap="large")

    with form_col:
        st.markdown("#### Stack Options")
        selections: dict[str, str] = {}

        for key, default in OPTION_DEFAULTS.items():
            opt_meta = og.get(key, {})
            choices   = opt_meta.get("choices", [default])
            label     = opt_meta.get("label", OPTION_LABELS.get(key, key))
            desc      = opt_meta.get("description", "")

            current = st.session_state.get(f"opt_{key}", default)
            idx = choices.index(current) if current in choices else 0

            val = st.selectbox(
                label,
                choices,
                index=idx,
                key=f"opt_{key}",
                help=desc,
            )
            selections[key] = val

        st.markdown("<br>", unsafe_allow_html=True)

        # App name / description
        app_name    = st.text_input("App Name", value="MyApp", key="app_name")
        description = st.text_area("Description (optional)", key="app_desc", height=80)

        # Compile Draft button
        if st.button("📐 Compile Draft", key="compile_btn", disabled=not project_id):
            with st.spinner("Compiling spec…"):
                result = api_post(
                    f"/api/v1/projects/{project_id}/specs/",
                    {
                        "selections":  selections,
                        "app_name":    app_name,
                        "description": description,
                    },
                )
            if result:
                st.session_state["last_spec"]    = result
                st.session_state["last_proj_id"] = project_id
                hash_short = result.get("canonical_hash", "")[:16]
                st.success(
                    f"✓ Draft v{result.get('version')} compiled — "
                    f"hash `{hash_short}…` "
                    f"Go to **Review Workspace** to lock it."
                )

        if not project_id:
            st.caption("⚠ Select or create a project first")

    # ── Impact Panel ──────────────────────────────────────────────────────────
    with impact_col:
        impact = compute_impact(selections)

        st.markdown("#### Live Impact Panel")

        # Hash preview (computed locally — full hash needs compile)
        import hashlib, json as _json  # noqa: E402
        preview_payload = {**selections, "app_name": app_name}
        preview_hash = hashlib.sha256(
            _json.dumps(preview_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:16]

        st.markdown(
            f'<div class="vf-hash">#{preview_hash}…</div>',
            unsafe_allow_html=True,
        )
        st.caption("Hash preview (first 16 chars of SHA-256)")
        st.markdown("<br>", unsafe_allow_html=True)

        # Compliance impact
        comp_colour = {
            "none": "blue", "gdpr": "yellow", "hipaa": "red", "pci": "red"
        }.get(impact["compliance"], "blue")
        comp_label = impact["compliance"].upper() if impact["compliance"] != "none" else "No compliance"
        st.markdown(
            f'**Compliance:** {pill(comp_label, comp_colour)}',
            unsafe_allow_html=True,
        )

        # Data classification
        dc_colour = {"internal": "blue", "confidential": "yellow", "restricted": "red"}.get(
            impact["data_class"], "blue"
        )
        st.markdown(
            f'**Data class:** {pill(impact["data_class"], dc_colour)}',
            unsafe_allow_html=True,
        )

        # MFA
        mfa_pill = pill("MFA required", "red") if impact["mfa_required"] else pill("MFA optional", "blue")
        st.markdown(f"**Auth:** {mfa_pill}", unsafe_allow_html=True)

        # PII fields
        if impact["pii_fields"]:
            pii_html = "".join(pill(f, "purple") for f in impact["pii_fields"])
            st.markdown(f"**PII fields:**<br>{pii_html}", unsafe_allow_html=True)
        else:
            st.markdown("**PII fields:** " + pill("none", "green"), unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Acceptance criteria count
        st.metric("Acceptance Criteria", impact["ac_count"], help="Tests the QA Gate will run")

        # Infrastructure notes
        if impact["infra_notes"]:
            st.markdown("**Infra included:**")
            for note in impact["infra_notes"]:
                st.markdown(f"- {note}")


# ──────────────────────────────────────────────────────────────────────────────
# Page 2: 🔍 Review Workspace
# ──────────────────────────────────────────────────────────────────────────────
def page_review_workspace() -> None:
    st.markdown('<div class="vf-section-header">🔍 Review Workspace</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="vf-section-sub">'
        'Inspect your compiled spec, compare it to defaults, '
        'and lock it to start generation.'
        '</div>',
        unsafe_allow_html=True,
    )

    # ── Spec source: last compiled or fetch from API ──────────────────────────
    last_spec    = st.session_state.get("last_spec")
    last_proj_id = st.session_state.get("last_proj_id")

    # Project selector (to load a spec from a different project)
    projects_data = api_get("/api/v1/projects/")
    projects = projects_data.get("items", []) if isinstance(projects_data, dict) else (projects_data or [])
    proj_names = [p["name"] for p in projects]
    proj_ids   = [p["id"]   for p in projects]

    selected_proj_name = st.selectbox("Project", proj_names if proj_names else ["— no projects —"], key="review_proj")
    project_id: str | None = None
    if proj_ids and selected_proj_name in proj_names:
        project_id = proj_ids[proj_names.index(selected_proj_name)]

    if project_id and project_id != last_proj_id:
        specs = api_get(f"/api/v1/projects/{project_id}/specs/")
        if specs and len(specs) > 0:
            last_spec = specs[-1]   # latest version
            st.session_state["last_spec"] = last_spec
            st.session_state["last_proj_id"] = project_id

    if not last_spec:
        st.info("No spec compiled yet. Go to **Spec Sheet**, set your options, and click **Compile Draft**.")
        return

    # ── Header ────────────────────────────────────────────────────────────────
    spec_data    = last_spec.get("spec_data", {})
    canon_hash   = last_spec.get("canonical_hash", "")
    version      = last_spec.get("version", 1)
    is_frozen    = last_spec.get("frozen_at") is not None

    hdr_col, status_col = st.columns([3, 1])
    with hdr_col:
        st.markdown(
            f"**Spec v{version}** &nbsp; "
            f'<span class="vf-hash">#{canon_hash[:24]}…</span>',
            unsafe_allow_html=True,
        )
    with status_col:
        if is_frozen:
            st.success("🔒 Frozen", icon="✅")
        else:
            st.warning("📝 Draft", icon="⚠️")

    st.divider()

    # ── Diff Cards (2-column grid) ────────────────────────────────────────────
    security = spec_data.get("security", {})
    nfr      = spec_data.get("nfr", {})
    api_m    = spec_data.get("api_model", {})
    ui_m     = spec_data.get("ui_model", {})
    domain   = spec_data.get("domain_model", {})
    wf       = spec_data.get("workflow", {})

    c1, c2 = st.columns(2)

    # ── Domain Model card ────────────────────────────────────────────────────
    with c1:
        highlight = domain.get("db_tier") != "postgres"
        spec_card(
            "🗃 Domain Model",
            [
                field_row("db_tier",  domain.get("db_tier", "—"),  domain.get("db_tier") != "postgres"),
                field_row("search",   domain.get("search_backend", "none"),  domain.get("search_backend") not in (None, "none")),
                field_row("entities", f"{domain.get('entity_count', 5)} base entities"),
                field_row("migrations", domain.get("migration_tool", "alembic")),
            ],
            highlight=highlight,
        )

        # API Model card
        spec_card(
            "⚡ API Model",
            [
                field_row("style",      api_m.get("style", "rest"),    api_m.get("style") != "rest"),
                field_row("versioning", api_m.get("versioning", "url-prefix")),
                field_row("pagination", api_m.get("pagination", "cursor")),
                field_row("rate-limit", api_m.get("rate_limiting", "per-tenant")),
            ],
        )

    with c2:
        # Security card — highlighted for any compliance tier
        comp = security.get("compliance_tier", "none")
        highlight_sec = comp != "none"
        spec_card(
            "🔐 Security",
            [
                field_row("auth",           security.get("auth_strategy", "jwt"),       security.get("auth_strategy") != "jwt"),
                field_row("compliance",     comp.upper() if comp != "none" else "none",  highlight_sec),
                field_row("mfa",            str(security.get("mfa_required", False)),   security.get("mfa_required", False)),
                field_row("data class",     security.get("data_classification", "internal"), highlight_sec),
                field_row("pii fields",     ", ".join(security.get("pii_fields", [])) or "none", bool(security.get("pii_fields"))),
            ],
            highlight=highlight_sec,
        )

        # NFR + Deploy card
        spec_card(
            "🚀 NFR & Deploy",
            [
                field_row("deploy",        nfr.get("deploy_target", "docker"),    nfr.get("deploy_target") != "docker"),
                field_row("ui framework",  ui_m.get("framework", "react"),        ui_m.get("framework") != "react"),
                field_row("async jobs",    str(wf.get("async_jobs_enabled", True))),
                field_row("slo p99",       nfr.get("response_time_slo_ms", "500") and f"{nfr.get('response_time_slo_ms', 500)}ms"),
                field_row("availability",  nfr.get("availability_slo", "99.5") and f"{nfr.get('availability_slo', 99.5)}%"),
            ],
        )

    # ── Acceptance Criteria ───────────────────────────────────────────────────
    st.markdown("#### Acceptance Criteria")
    ac_list = spec_data.get("acceptance", [])
    if ac_list:
        ac_html = ""
        for ac in ac_list:
            ac_html += (
                f'<div class="vf-ac-item">'
                f'<span class="vf-ac-id">{ac.get("id", "AC-?")}</span>'
                f'<span>{ac.get("description", "")}</span>'
                f'</div>'
            )
        st.markdown(f'<div class="vf-card">{ac_html}</div>', unsafe_allow_html=True)
    else:
        st.caption("No acceptance criteria — compile a spec first")

    st.divider()

    # ── Lock & Generate ───────────────────────────────────────────────────────
    st.markdown("#### 🔒 Lock & Generate")
    st.markdown(
        "Locking freezes this spec and enqueues a generation job. "
        "The canonical hash is stored — identical specs skip generation (P3 cache)."
    )

    lock_disabled = is_frozen

    if is_frozen:
        st.info("This spec is already locked. Check job status below or compile a new spec.")

    if st.button(
        "🔒 Lock & Generate" if not is_frozen else "✅ Already Locked",
        key="lock_btn",
        disabled=lock_disabled,
    ):
        if not project_id:
            st.error("No project selected")
        else:
            # Re-use the selections from spec_data to call generate
            spec_selections = _extract_selections_from_spec(spec_data)
            with st.spinner("Locking spec and enqueuing generation job…"):
                result = api_post(
                    f"/api/v1/projects/{project_id}/generate",
                    {
                        "selections":  spec_selections,
                        "app_name":    spec_data.get("app_name", "MyApp"),
                        "description": spec_data.get("description", ""),
                    },
                )
            if result:
                job_id   = result.get("job_id")
                cache_hit = result.get("cache_hit", False)
                st.session_state["active_job_id"]  = job_id
                st.session_state["active_proj_id"] = project_id
                if cache_hit:
                    st.success(f"♻ Cache hit (P3) — returning existing job `{job_id}`")
                else:
                    st.success(f"✓ Generation job enqueued — job `{job_id}`")

    # ── Job progress tracker ──────────────────────────────────────────────────
    active_job_id = st.session_state.get("active_job_id")
    if active_job_id:
        st.markdown("#### Job Progress")
        _render_job_progress(active_job_id)


def _extract_selections_from_spec(spec_data: dict) -> dict:
    """Best-effort: reverse-map spec_data fields back to selections dict."""
    s = spec_data.get("security", {})
    n = spec_data.get("nfr", {})
    u = spec_data.get("ui_model", {})
    a = spec_data.get("api_model", {})
    d = spec_data.get("domain_model", {})
    w = spec_data.get("workflow", {})
    return {
        "auth_strategy":   s.get("auth_strategy", "jwt"),
        "db_tier":         d.get("db_tier", n.get("db_tier", "postgres")),
        "ui_framework":    u.get("framework", "react"),
        "api_style":       a.get("style", "rest"),
        "deploy_target":   n.get("deploy_target", "docker"),
        "async_jobs":      "yes" if w.get("async_jobs_enabled", True) else "no",
        "compliance_tier": s.get("compliance_tier", "none"),
        "search_enabled":  "yes" if d.get("search_backend") not in (None, "none") else "no",
    }


def _render_job_progress(job_id: str) -> None:
    """Poll job status and events, render a progress bar + event list."""
    job = api_get(f"/api/v1/jobs/{job_id}")
    if not job:
        st.warning("Cannot fetch job status")
        return

    status = job.get("status", "unknown")
    STATUS_COLORS = {
        "queued":    "🟡",
        "running":   "🔵",
        "completed": "🟢",
        "failed":    "🔴",
        "cancelled": "⚫",
    }
    icon = STATUS_COLORS.get(status, "⚪")
    st.markdown(f"**Status:** {icon} `{status}`")

    # Fetch events
    events = api_get(f"/api/v1/jobs/{job_id}/events") or []
    if events:
        # Progress bar from last progress event
        progress_events = [e for e in events if e.get("event_type") == "progress"]
        if progress_events:
            last_pct = progress_events[-1].get("payload", {}).get("pct", 0)
            st.progress(last_pct / 100)

        # Event timeline
        st.markdown("**Event log:**")
        for e in events:
            evt   = e.get("event_type", "?")
            payload = e.get("payload", {})
            seq   = e.get("seq", "?")
            pct   = payload.get("pct", "")
            label = payload.get("label", "")
            pct_str = f" ({pct}%)" if pct else ""
            lbl_str = f" — {label}" if label else ""
            st.markdown(
                f'<div class="vf-ac-item">'
                f'<span class="vf-ac-id">#{seq}</span>'
                f'<span style="color:#7878aa">[{evt}]</span>'
                f'<span>{pct_str}{lbl_str}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    if status == "running":
        # Auto-refresh while job is running
        time.sleep(3)
        st.rerun()
    elif status == "completed":
        st.balloons()
        st.success("🎉 Generation complete!")


# ──────────────────────────────────────────────────────────────────────────────
# Sidebar navigation
# ──────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        """
        <div style="padding: 1rem 0 1.5rem; text-align: center;">
            <div style="font-size: 2rem;">🔥</div>
            <div style="font-size: 1.2rem; font-weight: 700; color: #c4b5fd;">VibeForge</div>
            <div style="font-size: 0.72rem; color: #7878aa; margin-top: 4px;">
                Spec → Code Platform
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    page = st.radio(
        "Navigate",
        ["🧬 Spec Sheet", "🔍 Review Workspace"],
        key="nav",
        label_visibility="collapsed",
    )

    st.divider()

    # Health check widget
    try:
        health = httpx.get(f"{API_BASE}/health", timeout=3.0).json()
        api_ok    = health.get("status") == "healthy"
        pg_ok     = health.get("checks", {}).get("postgres") == "ok"
        redis_ok  = health.get("checks", {}).get("redis") == "ok"
    except Exception:
        api_ok = pg_ok = redis_ok = False

    st.markdown("**System Status**")
    st.markdown(
        f"{'🟢' if api_ok else '🔴'} API &nbsp;&nbsp;"
        f"{'🟢' if pg_ok else '🔴'} Postgres &nbsp;&nbsp;"
        f"{'🟢' if redis_ok else '🔴'} Redis",
        unsafe_allow_html=True,
    )

    st.divider()
    st.caption(f"API: `{API_BASE}`")
    st.caption(f"Keycloak: `{KC_BASE}`")


# ──────────────────────────────────────────────────────────────────────────────
# Router
# ──────────────────────────────────────────────────────────────────────────────
if page == "🧬 Spec Sheet":
    page_spec_sheet()
else:
    page_review_workspace()
