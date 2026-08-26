# 🏦 VibeForge — Banking Vertical Showcase Script (Demo for Tirumala)

**Objective**: Demonstrate how VibeForge autonomously generates an enterprise-grade, RBI-compliant Core Banking & Payment microservice in under 60 seconds with 0 syntax errors, automated SBOM generation, and continuous cost optimization.

---

## 🎬 Act 1: The Spec Sheet (Zero-Token Determinism)
1. Open the UI at `http://localhost:8501` (or `https://ui-5rpnepdwnq-uc.a.run.app`).
2. Select Vertical: **`Banking`**.
3. Choose Stack:
   - **Auth Strategy**: `OAuth2 OIDC / Keycloak`
   - **Database**: `PostgreSQL HA`
   - **Payment Rails**: `UPI / IMPS / NEFT`
   - **Compliance Level**: `RBI (Reserve Bank of India Guidelines)`
4. Point out the **Live Impact Panel**:
   - Deterministic SHA-256 hash preview (`#a1b2c3d4...`) updates instantly without spending a single LLM token.

---

## 🎬 Act 2: Completeness Validation & Gap Q&A (Layer 2 & 3)
1. Click **Draft Compile**.
2. Show the **Completeness Validator (Contract C15)**:
   - VibeForge detects missing parameters (e.g. *Cool-off period for new beneficiaries* & *Daily IMPS limit*).
   - The job pauses in `paused_human` state instead of blindly synthesizing incomplete code.
3. Submit answers via UI / API:
   - *Cool-off Period*: `24 hours`
   - *Daily Limit*: `100,000 INR`
4. The job automatically resumes into the generation queue!

---

## 🎬 Act 3: Multi-Agent Synthesis & Sandbox Gate (Contract C5 & C14)
1. Watch the live SSE Job Console:
   - **Planner Agent**: Builds the sequential DAG (Entities → Repositories → Services → Controllers).
   - **Synthesizer Agent**: Generates clean Spring Boot code with `@Transactional`, `@Positive`, and KYC validation.
   - **Sandbox Gate**: Compiles code and runs unit tests.
2. Highlight **Self-Healing**:
   - If a test fails, Reviewer AI isolates the bug and Fixer AI repairs it in 1 iteration.
   - **Escalation Memory (Contract C14)**: Caches the solution signature for **$0.00 instant reuse**.

---

## 🎬 Act 4: Enterprise Delivery & SBOM
1. View the delivery card:
   - **Private Gitea Repository**: Pushed directly to version control.
   - **MinIO S3 Bundle**: Downloadable `.zip` archive.
   - **CycloneDX 1.5 SBOM**: Standardized `sbom.json` listing all dependencies, licenses, and PURLs for security audits.

---

## 🎬 Act 5: Budget & Cost Dashboard
1. Open `/dashboards`.
2. Showcase the financial metrics:
   - **Average cost per build**: $0.001 (local models) vs $0.50 (commercial).
   - **Memory hit savings**: 100% cost reduction on recurring compilation fixes.
   - **Total Tenant Isolation**: Zero cross-tenant data leakage (Contract C1).
