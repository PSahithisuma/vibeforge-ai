"""
VibeForge — Application Specification IR (Intermediate Representation)
======================================================================
This is the single source of truth. Every agent, orchestrator, and
renderer reads from and writes to this schema. Nothing else is the
contract.

Rules:
  - Every model is immutable once frozen (spec.frozen = True)
  - canonical_hash is computed from a normalized JSON snapshot
    (volatile fields like created_at are excluded before hashing)
  - Version increments on every accepted spec_delta
  - Provenance tracks where every field value came from
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Enumerations ──────────────────────────────────────────────────────────────

class Vertical(str, Enum):
    ECOMMERCE   = "ecommerce"
    BANKING     = "banking"
    LOGISTICS   = "logistics"
    CUSTOM      = "custom"


class BackendStack(str, Enum):
    JAVA_SPRING = "java_spring"
    PYTHON_FASTAPI = "python_fastapi"
    DOTNET      = "dotnet"
    NODEJS      = "nodejs"
    GO          = "go"


class FrontendStack(str, Enum):
    REACT       = "react"
    VUE         = "vue"
    ANGULAR     = "angular"


class DatabaseType(str, Enum):
    POSTGRESQL  = "postgresql"
    MYSQL       = "mysql"
    SQLSERVER   = "sqlserver"
    MONGODB     = "mongodb"


class SpecStatus(str, Enum):
    DRAFT       = "draft"
    PROPOSED    = "proposed"
    IN_REVIEW   = "in_review"
    APPROVED    = "approved"
    FROZEN      = "frozen"


class AuthMode(str, Enum):
    JWT         = "jwt"
    SESSION     = "session"
    OAUTH2      = "oauth2"
    API_KEY     = "api_key"


class HttpMethod(str, Enum):
    GET     = "GET"
    POST    = "POST"
    PUT     = "PUT"
    PATCH   = "PATCH"
    DELETE  = "DELETE"


class FieldType(str, Enum):
    STRING      = "string"
    INTEGER     = "integer"
    DECIMAL     = "decimal"
    BOOLEAN     = "boolean"
    DATETIME    = "datetime"
    UUID        = "uuid"
    JSON        = "json"
    MONEY       = "money"     # maps to BigDecimal+currency in Java, Decimal in Python


class PIIClass(str, Enum):
    NONE        = "none"
    SENSITIVE   = "sensitive"   # email, phone
    RESTRICTED  = "restricted"  # PAN, Aadhaar, card numbers


class ComplianceFramework(str, Enum):
    PCI_DSS     = "pci_dss"
    GDPR        = "gdpr"
    RBI         = "rbi"
    ISO27001    = "iso27001"


# ── Provenance ────────────────────────────────────────────────────────────────

class ProvenanceEntry(BaseModel):
    """Tracks where a spec field value came from."""
    json_path: str          = Field(..., description="JSONPath to the field e.g. $.domain_model.entities[0].name")
    source_type: str        = Field(..., description="option_selection | gap_answer | amendment | brd_upload | manual")
    source_id: str          = Field(..., description="option_id, message_id, amendment_id, or doc_id")
    value_snapshot: Any     = Field(..., description="The value at the time of this provenance entry")
    recorded_at: datetime   = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Domain Model ──────────────────────────────────────────────────────────────

class EntityField(BaseModel):
    name: str
    field_type: FieldType
    required: bool              = True
    pii_class: PIIClass         = PIIClass.NONE
    description: str            = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    # e.g. {"max_length": 255, "min": 0, "unique": true}


class EntityRelationship(BaseModel):
    target_entity: str
    relationship_type: str      # "one_to_many" | "many_to_many" | "many_to_one"
    foreign_key: str            = ""
    cascade: bool               = False


class Entity(BaseModel):
    name: str
    description: str            = ""
    fields: list[EntityField]   = Field(default_factory=list)
    relationships: list[EntityRelationship] = Field(default_factory=list)
    audit_fields: bool          = True  # adds created_at, updated_at, created_by
    soft_delete: bool           = False # adds deleted_at instead of hard delete


class DomainModel(BaseModel):
    entities: list[Entity]      = Field(default_factory=list)
    enumerations: dict[str, list[str]] = Field(default_factory=dict)
    # e.g. {"OrderStatus": ["PENDING", "CONFIRMED", "SHIPPED", "DELIVERED"]}


# ── API Model ─────────────────────────────────────────────────────────────────

class RequestParam(BaseModel):
    name: str
    param_type: str             # "path" | "query" | "body" | "header"
    field_type: FieldType
    required: bool              = True
    description: str            = ""


class ApiEndpoint(BaseModel):
    endpoint_id: str            = Field(default_factory=lambda: f"ep_{uuid4().hex[:8]}")
    method: HttpMethod
    path: str                   # e.g. "/api/v1/orders/{id}"
    summary: str
    description: str            = ""
    auth_required: bool         = True
    roles: list[str]            = Field(default_factory=list)  # ["CUSTOMER", "ADMIN"]
    request_params: list[RequestParam] = Field(default_factory=list)
    request_body_entity: str    = ""   # entity name, empty if no body
    response_entity: str        = ""   # entity name or "list[<entity>]"
    tags: list[str]             = Field(default_factory=list)
    acceptance_criteria_ids: list[str] = Field(default_factory=list)


class ApiModel(BaseModel):
    base_path: str              = "/api/v1"
    auth_mode: AuthMode         = AuthMode.JWT
    versioning_strategy: str    = "uri"  # "uri" | "header" | "query_param"
    endpoints: list[ApiEndpoint] = Field(default_factory=list)
    rate_limiting: bool         = True
    pagination_style: str       = "cursor"  # "cursor" | "offset"


# ── UI Model ──────────────────────────────────────────────────────────────────

class UIComponent(BaseModel):
    component_id: str
    component_type: str         # "table" | "form" | "chart" | "card" | "list"
    title: str
    data_source_endpoint: str   = ""  # endpoint_id
    actions: list[str]          = Field(default_factory=list)
    # e.g. ["create", "edit", "delete", "export"]


class UIScreen(BaseModel):
    screen_id: str
    name: str
    route: str                  # e.g. "/orders"
    roles: list[str]            = Field(default_factory=list)
    components: list[UIComponent] = Field(default_factory=list)
    description: str            = ""


class UIModel(BaseModel):
    screens: list[UIScreen]     = Field(default_factory=list)
    frontend_stack: FrontendStack = FrontendStack.REACT
    design_system: str          = "tailwind"
    theme: dict[str, str]       = Field(default_factory=dict)


# ── Workflow Model ────────────────────────────────────────────────────────────

class WorkflowTransition(BaseModel):
    from_state: str
    to_state: str
    trigger: str                # event name e.g. "PAYMENT_CONFIRMED"
    guard: str                  = ""  # condition expression
    actions: list[str]          = Field(default_factory=list)
    # e.g. ["send_confirmation_email", "update_inventory"]


class StateMachine(BaseModel):
    name: str
    entity: str                 # which entity this state machine governs
    state_field: str            = "status"
    initial_state: str          = ""
    states: list[str]           = Field(default_factory=list)
    transitions: list[WorkflowTransition] = Field(default_factory=list)


class WorkflowModel(BaseModel):
    state_machines: list[StateMachine] = Field(default_factory=list)
    background_jobs: list[dict[str, Any]] = Field(default_factory=list)
    # e.g. [{"name": "send_order_reminders", "schedule": "0 9 * * *"}]


# ── Integration Model ─────────────────────────────────────────────────────────

class ConnectorOperation(BaseModel):
    operation_id: str
    name: str
    description: str            = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)


class Integration(BaseModel):
    integration_id: str         = Field(default_factory=lambda: f"int_{uuid4().hex[:8]}")
    name: str                   # e.g. "Razorpay"
    connector_ref: str          # pack connector id
    auth_mode: str              = "api_key"  # "api_key" | "oauth2" | "basic"
    operations: list[ConnectorOperation] = Field(default_factory=list)
    mock_server: bool           = True  # use Prism/WireMock in sandbox
    environment_vars: list[str] = Field(default_factory=list)
    # e.g. ["RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"]


class IntegrationModel(BaseModel):
    integrations: list[Integration] = Field(default_factory=list)


# ── Security Model ────────────────────────────────────────────────────────────

class Role(BaseModel):
    name: str
    description: str            = ""
    permissions: list[str]      = Field(default_factory=list)
    # e.g. ["orders:read", "orders:write", "admin:users"]


class SecurityModel(BaseModel):
    auth_provider: str          = "keycloak"
    auth_mode: AuthMode         = AuthMode.JWT
    roles: list[Role]           = Field(default_factory=list)
    pii_fields: list[str]       = Field(default_factory=list)
    # JSONPaths to PII fields e.g. ["$.domain_model.entities[?(@.name=='Customer')].fields[?(@.name=='email')]"]
    security_headers: bool      = True
    csrf_protection: bool       = True
    rate_limiting: bool         = True
    session_timeout_minutes: int = 60


# ── Compliance Model ──────────────────────────────────────────────────────────

class ComplianceRule(BaseModel):
    rule_id: str
    framework: ComplianceFramework
    description: str
    semgrep_rule_ref: str       = ""  # reference to a Semgrep rule in the pack
    generation_constraint: str  = ""  # constraint injected into synthesis prompt
    auto_enforced: bool         = True


class ComplianceModel(BaseModel):
    frameworks: list[ComplianceFramework] = Field(default_factory=list)
    rules: list[ComplianceRule]  = Field(default_factory=list)
    audit_logging: bool          = True
    data_retention_days: int     = 90


# ── NFR (Non-Functional Requirements) ────────────────────────────────────────

class NFR(BaseModel):
    response_time_p99_ms: int   = 500
    throughput_rps: int         = 100
    availability_percent: float = 99.9
    max_payload_size_mb: int    = 10
    database_pool_size: int     = 20
    cache_ttl_seconds: int      = 300
    logging_level: str          = "INFO"
    metrics_enabled: bool       = True
    tracing_enabled: bool       = True


# ── Acceptance Criteria ───────────────────────────────────────────────────────

class AcceptanceCriterion(BaseModel):
    criterion_id: str           = Field(default_factory=lambda: f"ac_{uuid4().hex[:8]}")
    feature: str
    scenario: str               # Gherkin-style: "Given ... When ... Then ..."
    endpoint_ids: list[str]     = Field(default_factory=list)
    priority: str               = "must"  # "must" | "should" | "could"
    automated: bool             = True


# ── Stack Configuration ───────────────────────────────────────────────────────

class StackConfig(BaseModel):
    backend: BackendStack       = BackendStack.JAVA_SPRING
    frontend: FrontendStack     = FrontendStack.REACT
    database: DatabaseType      = DatabaseType.POSTGRESQL
    stack_profile_id: str       = ""  # references the stack profile registry
    stack_profile_version: str  = ""


# ── Root ApplicationSpec ──────────────────────────────────────────────────────

class ApplicationSpec(BaseModel):
    """
    The root Spec IR. Every agent reads this. Nothing else is the contract.

    Lifecycle:
        DRAFT → (completeness validator) → PROPOSED → (review workspace)
        → IN_REVIEW → (dual-control approval) → APPROVED
        → (Lock & Generate) → FROZEN

    Once FROZEN, this object is immutable. Edits create a new version.
    """

    # Identity
    spec_id: UUID               = Field(default_factory=uuid4)
    project_id: UUID            = Field(default_factory=uuid4)
    tenant_id: UUID             = Field(default_factory=uuid4)
    spec_version: int           = Field(default=1, ge=1)

    # Status
    status: SpecStatus          = SpecStatus.DRAFT
    frozen: bool                = False

    # Vertical and stack
    vertical: Vertical          = Vertical.ECOMMERCE
    domain_pack_id: str         = ""
    stack: StackConfig          = Field(default_factory=StackConfig)

    # The eight specification sections
    domain_model: DomainModel           = Field(default_factory=DomainModel)
    api_model: ApiModel                 = Field(default_factory=ApiModel)
    ui_model: UIModel                   = Field(default_factory=UIModel)
    workflow_model: WorkflowModel       = Field(default_factory=WorkflowModel)
    integration_model: IntegrationModel = Field(default_factory=IntegrationModel)
    security_model: SecurityModel       = Field(default_factory=SecurityModel)
    compliance_model: ComplianceModel   = Field(default_factory=ComplianceModel)
    nfr: NFR                            = Field(default_factory=NFR)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)

    # Provenance: every field change is recorded
    provenance: list[ProvenanceEntry]   = Field(default_factory=list)

    # The content hash — computed, never set manually
    canonical_hash: str         = ""

    # Audit
    created_at: datetime        = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime        = Field(default_factory=lambda: datetime.now(timezone.utc))
    locked_at: datetime | None  = None
    locked_by: str              = ""
    approved_by: str            = ""

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("spec_version")
    @classmethod
    def version_must_be_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("spec_version must be >= 1")
        return v

    @model_validator(mode="after")
    def frozen_spec_must_have_hash(self) -> ApplicationSpec:
        if self.frozen and not self.canonical_hash:
            raise ValueError("A frozen spec must have a canonical_hash. Call spec.freeze() first.")
        return self

    # ── Methods ───────────────────────────────────────────────────────────────

    def compute_hash(self) -> str:
        """
        Compute a deterministic SHA-256 hash over the spec content.
        Volatile fields (spec_id, tenant_id, timestamps, canonical_hash,
        provenance, status) are excluded so the same logical spec always
        produces the same hash regardless of when or by whom it was created.
        """
        VOLATILE_FIELDS = {
            "spec_id", "project_id", "tenant_id",
            "created_at", "updated_at", "locked_at", "locked_by",
            "approved_by", "canonical_hash", "provenance", "status",
            "frozen", "spec_version",
        }

        raw = self.model_dump(mode="json")
        for field in VOLATILE_FIELDS:
            raw.pop(field, None)

        # Sort keys for determinism
        serialized = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(serialized.encode()).hexdigest()

    def freeze(self) -> ApplicationSpec:
        """
        Freeze the spec: compute hash, set status to FROZEN, mark frozen=True.
        Returns a new frozen copy — does not mutate in place.
        """
        if self.frozen:
            raise ValueError(f"Spec v{self.spec_version} is already frozen.")

        new_hash = self.compute_hash()
        now = datetime.now(timezone.utc)

        return self.model_copy(update={
            "frozen": True,
            "status": SpecStatus.FROZEN,
            "canonical_hash": new_hash,
            "locked_at": now,
            "updated_at": now,
        })

    def apply_delta(self, delta: "SpecDelta") -> ApplicationSpec:
        """
        Apply a validated SpecDelta and return a new spec version.
        Never mutates in place. Increments spec_version. Records provenance.
        Raises if the spec is frozen.
        """
        if self.frozen:
            raise ValueError(
                f"Cannot apply delta to frozen spec v{self.spec_version}. "
                "Create a new version instead."
            )

        # Deep merge the delta patch into the current spec data
        current_data = self.model_dump(mode="json")
        _deep_merge(current_data, delta.patch)

        # Append provenance entries
        # Accepts both ProvenanceEntry models and plain dicts (from OptionDelta)
        provenance = list(self.provenance)
        for entry in delta.provenance_entries:
            if isinstance(entry, dict):
                # Dict may come from OptionDelta.provenance_entries (raw binding info)
                # Build a valid ProvenanceEntry with safe defaults for missing fields
                provenance.append(ProvenanceEntry(
                    json_path=entry.get("json_path", ""),
                    source_type=entry.get("source_type", "option_selection"),
                    source_id=entry.get("source_id", getattr(delta, "amendment_id", "unknown")),
                    value_snapshot=str(entry.get("value_snapshot", entry.get("value", "")))[:200],
                ))
            else:
                provenance.append(entry)

        # Auto-convert OptionDelta to SpecDelta if needed (duck typing)
        # OptionDelta has patch, provenance_entries, amendment_id, impact_summary
        now = datetime.now(timezone.utc)
        current_data.update({
            "spec_version": self.spec_version + 1,
            "status": SpecStatus.IN_REVIEW,
            "updated_at": now.isoformat(),
            "canonical_hash": "",  # cleared until next freeze
            "frozen": False,
            "provenance": [p.model_dump(mode="json") for p in provenance],
        })

        return ApplicationSpec.model_validate(current_data)

    def summarize(self) -> dict[str, Any]:
        """
        Compact summary for logging, SSE events, and the live impact panel.
        """
        return {
            "spec_id": str(self.spec_id),
            "spec_version": self.spec_version,
            "status": self.status.value,
            "frozen": self.frozen,
            "vertical": self.vertical.value,
            "stack": f"{self.stack.backend.value} + {self.stack.frontend.value} + {self.stack.database.value}",
            "entity_count": len(self.domain_model.entities),
            "endpoint_count": len(self.api_model.endpoints),
            "screen_count": len(self.ui_model.screens),
            "integration_count": len(self.integration_model.integrations),
            "acceptance_criteria_count": len(self.acceptance_criteria),
            "canonical_hash": self.canonical_hash[:12] + "..." if self.canonical_hash else "not set",
        }


# ── Spec Delta (the output of the Spec Editor Agent) ─────────────────────────

class SpecDelta(BaseModel):
    """
    A validated, structured change to the spec produced by the Spec Editor Agent.
    Never applied directly — always goes through the diff card accept/reject flow.

    The patch is a dict of JSONPath → new_value mappings that get deep-merged
    into the spec. The provenance_entries record the source of each change.
    """

    delta_id: str               = Field(default_factory=lambda: f"delta_{uuid4().hex[:8]}")
    amendment_id: str           = ""    # the amendment that triggered this
    patch: dict[str, Any]       = Field(default_factory=dict)
    # Simple nested dict that gets deep-merged e.g.:
    # {"domain_model": {"entities": [{"name": "Review", "fields": [...]}]}}

    provenance_entries: list[ProvenanceEntry] = Field(default_factory=list)
    impact_summary: str         = ""    # plain English for the diff card
    new_entity_count: int       = 0
    new_endpoint_count: int     = 0
    compliance_implications: list[str] = Field(default_factory=list)

    created_at: datetime        = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> None:
    """
    Recursively merge override into base in-place.
    Lists are replaced (not extended) — the override is authoritative.
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def make_empty_spec(
    tenant_id: UUID,
    project_id: UUID,
    vertical: Vertical = Vertical.ECOMMERCE,
    domain_pack_id: str = "",
) -> ApplicationSpec:
    """
    Factory function — creates a fresh DRAFT spec with sensible defaults.
    Use this everywhere instead of constructing ApplicationSpec() directly.
    """
    return ApplicationSpec(
        tenant_id=tenant_id,
        project_id=project_id,
        vertical=vertical,
        domain_pack_id=domain_pack_id,
        status=SpecStatus.DRAFT,
    )
