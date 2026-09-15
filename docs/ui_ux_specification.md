# 🎨 VibeForge Enterprise — UI/UX Specification Document (v1.0)

## Complete Component, Layout & Interaction Guide
**Target Audience**: Frontend Engineers, Designers, Platform Architects
**Reference Design**: media_1789468202352.jpg (VibeForge Enterprise 3-Pane Dashboard)

---

## 1. Executive Layout Architecture

The application adopts a **Modern Dark-Foundry / High-Contrast Slate Theme** divided into four persistent structural zones:

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────┐
│  ZONE 1: GLOBAL HEADER BAR (Status, Tenant Switcher, Project Selector, Time/User)               │
├───────────────┬──────────────────────────────────┬───────────────────────────────────────────────┤
│  ZONE 2:      │  ZONE 3: THE MAIN WORKSPACE      │                                               │
│  LEFT NAV     ├──────────────────────────────────┼───────────────────────────────────────────────┤
│  SIDEBAR      │  PANEL 1: SPEC CONFIGURATOR      │  PANEL 2: LIVING BLUEPRINT & MISSION CONTROL  │
│  (60px/220px) │  (Select-Don't-Type Engine)      │  (ERD, API Routes, Agents, Live Logs)        │
│               ├──────────────────────────────────┴───────────────────────────────────────────────┤
│               │  PANEL 3: IMPACT & ECONOMIC INTELLIGENCE (ROI Badge, Code Preview, Quick-Start)  │
├───────────────┴──────────────────────────────────────────────────────────────────────────────────┤
│  ZONE 4: BOTTOM PERSISTENT AUDIT & ACTION BAR (Canonical Hash, Compliance Badge, Lock CTA)       │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Design Tokens & Styling Guide

* **Color Palette**:
  * **Background Deep**: `#0B0F17` (Main body background)
  * **Surface Card**: `#161F30` (Panels, modal cards, and containers)
  * **Border Subtle**: `#1E293B` (1px borders for all cards)
  * **Primary Accent**: `#6366F1` (Indigo / Violet for buttons and active states)
  * **Success Accent**: `#10B981` (Emerald Green for **$0.00 Savings** and 100% Green QA Passes)
  * **Warning Accent**: `#F59E0B` (Amber for Self-Healing Fix iterations and gaps)
  * **Error Accent**: `#EF4444` (Rose Red for compiler errors and failed tests)
* **Typography**:
  * **Primary UI Font**: `Inter, -apple-system, sans-serif`
  * **Code & Terminal Font**: `JetBrains Mono, 'Fira Code', monospace`

---

## 3. Zone 1: Global Header Bar

Located at the very top of the screen (height: `64px`, border-bottom: `1px solid #1E293B`):
1. **Brand Logo**: VibeForge Icon (Glowing Blue Hexagon) + `VibeForge Enterprise` + subtitle `From Ideas to Intelligent Systems`.
2. **Project Selector**: Dropdown showing active project (e.g., `Project: VibeBank Core ▾`).
3. **Tenant Switcher**: Multi-tenant selector (e.g., `Tenant: ICICI-Demo ▾`) — triggers PostgreSQL RLS switch.
4. **Live Infrastructure Indicator**: Green badge: `● 17 Services Live` (with tooltip listing API, Sandbox, Qdrant, etc.).
5. **Universal Search**: `Search anything... [⌘K]`.
6. **User Profile & Clock**: Notification Bell + Avatar (`S Sahithi`) + Live Date/Time (`Mon, 15 Sep 2026, 10:30 AM`).

---

## 4. Zone 2: Left Navigation Sidebar

Persistent collapsible dark navigation bar (width: `220px`):
1. **⚡ Domain Accelerator** (Default Active): The 3-pane generation studio.
2. **📁 My Projects**: Grid/list of generated apps with Gitea repo links and ZIP downloads.
3. **📑 Templates**: Pre-built enterprise presets (NeoBank, Quick-Commerce, Logistics Fleet).
4. **🤖 Agent Pipeline**: Live full-screen LangGraph DAG visualizer.
5. **📊 Cost & Analytics**: Executive ROI dashboard with spend history charts.
6. **🛡️ Compliance**: CycloneDX 1.5 SBOM explorer, Semgrep SAST reports, and audit logs.
7. **⚙️ Settings**: LLM API keys (Groq, Gemini, Claude), Git credentials, and tenant quotas.
8. **Bottom Graphic Banner**: Illustration card `Build Smarter Banking Systems with AI Agents (Ideas ➔ Spec ➔ Code ➔ Impact)`.

---

## 5. Zone 3: Main Workspace (The 3 Panels)

### PANEL 1: Configure Your Spec (Input Column — Width: 320px)
* **Step 1 Badge**: `1 Configure Your Spec (Select domain, archetypes and compliance)`.
* **Industry Domain (Radio Group)**:
  * 🏦 **Banking (RBI)** [Selected]
  * 🛒 **E-Commerce**
  * 🚚 **Logistics**
  * ⚙️ **Custom YAML**
* **Business Model Archetypes (Multi-Select Checkboxes)**:
  * ☑ `Core Savings`
  * ☑ `UPI / IMPS Rails`
  * ☑ `24h Cooldown`
  * ☐ `Lending & Credit`
  * ☐ `KYC & Onboarding`
  * ☐ `Fraud Detection`
* **Compliance Tier (Radio Group)**:
  * 🔘 `RBI Guidelines` [Selected]
  * ⚪ `PCI-DSS Level 1`
  * ⚪ `GDPR Compliant`
* **Technology Stack Selectors (Dropdowns)**:
  * Backend: `Java Spring` ▾
  * Database: `PostgreSQL` ▾
  * Auth Mode: `JWT` ▾
* **Quick Action Buttons**: `[ ⎘ Load Preset ]` `[ ↺ Reset to Empty ]`

### PANEL 2: Living Spec Blueprint & Mission Control (Center Column — Flex 1)
* **Header Bar**: Title `2 Living Spec Blueprint & Agent Pipeline` + `[ ⛶ Open in Fullscreen ]`.
* **Top Navigation Tabs (6 View Modes)**:
  1. `[ ERD View ]` (Default):
     * Toolbar: `Zoom (+/-)`, `Fit Screen`, `Lock`, `[ ⤓ Export ERD ▾ ]`.
     * Interactive Canvas: Node cards (`Customer`, `Account`, `Transaction`, `Beneficiary`, `KYC_Document`) with `[PK]` and `[FK]` markers and `1:N` relational SVG connectors.
  2. `[ API Endpoints ]`: Search bar + Method pills (`GET`, `POST`, `PUT`, `DELETE`). Table showing route path, controller class, and auth/compliance tags.
  3. `[ Data Model ]`: Tabular schema dictionary showing column types, constraints (`unique`, `min`), and PII classification (`Restricted`, `Sensitive`).
  4. `[ Agents ]`: Roster cards showing Planner, Groq Synthesizer (~300 tok/sec), Sandbox QA, and Reviewer/Fixer status.
  5. `[ Requirements ]`: Gherkin scenario checklist (`Feature: RBI Core Banking Transfer`) with passing assertions.
  6. `[ AI Chat ]`: Conversational escape hatch producing structured **Diff Cards** for code amendments.
* **Bottom Section: Mission Control Pipeline Stepper**:
  * Stepper nodes: `Plan (Completed)` ➔ `Scaffold $0 (Completed)` ➔ `Synthesize (Groq ~300t/s - In Progress)` ➔ `QA Gate` ➔ `Self-Healing` ➔ `Package` ➔ `Deliver`.
* **Real-Time Agent Logs (SSE Terminal Window)**:
  * Monospace dark terminal with `Auto Scroll [ON]` and `Clear` buttons streaming live agent actions.

### PANEL 3: Impact & Economic Intelligence (Right Column — Width: 340px)
* **Step 3 Badge**: `3 Impact & Economic Intelligence (Real-time value, compliance and build insights)`.
* **The Hero Savings Card**: Emerald-tinted glowing card with coin icon. Headline: `TODAY'S SAVINGS: $0.00`, Sub-badge: `100% Zero-Token Hit`, Secondary metric: `Avoided Spend: $148.50`.
* **Spec Live Impact Counters**: Entities: `2 Active`, Endpoints: `7 Generated`, Compliance: `RBI Tier 1`, Scaffolding: `85% Pure $0`, Est. Build Time: `~45 seconds`, Est. Cost: `$0.00`.
* **Live Code Preview Window**: File dropdown `AccountService.java ▾`, syntax-highlighted code editor preview displaying verified `@Service`, `@Transactional`, and `BigDecimal amount` logic.
* **Quick Start Box**: One-click copy button with `git clone` and `mvn spring-boot:run`.

---

## 6. Zone 4: Bottom Persistent Audit Bar

Fixed footer bar (height: `52px`, border-top: `1px solid #1E293B`):
* **Left**: Status indicator: `✅ STATUS: 100% RBI Compliant`.
* **Center**: Deterministic snapshot hash: `CANONICAL HASH: 58ccdba23de7... [⎘ Copy]`.
* **Right CTAs**: `[ 🔒 Lock & Generate ]` (Solid Dark Blue) and `[ ⤓ Download .ZIP ]` (Solid Vibrant Blue).

---

## 7. Backend API Binding Matrix

| UI Component / Action | FastAPI Endpoint | HTTP Method |
|---|---|:---:|
| Select Option Checkbox | `/api/v1/projects/{id}/sheet` | `PUT` |
| Live Impact Counters | `/api/v1/projects/{id}/spec` | `GET` |
| Lock & Generate CTA | `/api/v1/specs/{id}/lock` | `POST` |
| Real-Time Terminal Logs | `/api/v1/jobs/{id}/events` | `GET (SSE)` |
| Live Code / Artifact View | `/api/v1/delivery/preview/{id}` | `GET` |
| Download .ZIP Bundle | `/api/v1/jobs/{id}/artifacts` | `GET` |
| Cost Dashboard ROI Numbers | `/api/v1/budget/{tenant_id}/summary` | `GET` |