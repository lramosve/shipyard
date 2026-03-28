"""Structured architecture plan schema for the architect worker.

The architect produces a JSON plan matching these Pydantic models.
The coder consumes it as a section-by-section checklist.
The reviewer validates output against it.
"""

from __future__ import annotations

import json
import logging
import re
from enum import Enum

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sub-models for each plan section
# ---------------------------------------------------------------------------

class TechStackItem(BaseModel):
    name: str = Field(description="Package or tool name (e.g. 'express')")
    version: str = Field(default="", description="Version constraint (e.g. '^4.21')")
    role: str = Field(description="Why this dep is needed (e.g. 'HTTP framework')")


class DatabaseTable(BaseModel):
    name: str
    columns: list[str] = Field(description="Column definitions, e.g. 'id UUID PRIMARY KEY'")
    relationships: list[str] = Field(default_factory=list, description="FK or association descriptions")
    indexes: list[str] = Field(default_factory=list)


class ApiEndpoint(BaseModel):
    method: str = Field(description="HTTP method: GET, POST, PATCH, DELETE")
    path: str = Field(description="Route path, e.g. '/api/documents/:id'")
    purpose: str = Field(description="What this endpoint does")
    request_shape: str = Field(default="", description="Shape of request body")
    response_shape: str = Field(default="", description="Shape of response body")
    auth_required: bool = True
    middleware: list[str] = Field(default_factory=list)


class ComponentNode(BaseModel):
    name: str = Field(description="Component or module name")
    file_path: str = Field(description="Where this file lives, e.g. 'src/components/Dashboard.tsx'")
    purpose: str = Field(default="")
    children: list[str] = Field(default_factory=list, description="Child component names")
    max_lines: int = Field(default=300)


class FileEntry(BaseModel):
    path: str = Field(description="Relative file path")
    purpose: str = Field(description="What this file contains")
    max_lines: int = Field(default=300)


class SecurityRequirement(BaseModel):
    category: str = Field(description="e.g. 'authentication', 'input_validation', 'csrf'")
    description: str
    implementation_notes: str = Field(default="")


class BusinessRule(BaseModel):
    name: str
    description: str
    source_module: str = Field(default="", description="Where in the original this logic lives")


# ---------------------------------------------------------------------------
# Top-level plan model
# ---------------------------------------------------------------------------

class ArchitecturePlan(BaseModel):
    """Structured output from the architect worker."""

    project_name: str = ""
    summary: str = Field(default="", description="1-3 sentence overview of what is being built")

    tech_stack: list[TechStackItem] = Field(default_factory=list)
    database_tables: list[DatabaseTable] = Field(default_factory=list)
    api_endpoints: list[ApiEndpoint] = Field(default_factory=list)
    components: list[ComponentNode] = Field(default_factory=list)
    file_structure: list[FileEntry] = Field(default_factory=list)
    security_requirements: list[SecurityRequirement] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    accessibility_notes: list[str] = Field(default_factory=list)
    critical_rules: list[str] = Field(
        default_factory=list,
        description="Hard constraints the coder must never violate",
    )

    def validate_completeness(self) -> list[str]:
        """Return a list of warnings for empty sections."""
        warnings = []
        if not self.tech_stack:
            warnings.append("tech_stack is empty — specify languages, frameworks, and key deps")
        if not self.file_structure:
            warnings.append("file_structure is empty — define the directory layout")
        if not self.api_endpoints and not self.components:
            warnings.append("Both api_endpoints and components are empty — at least one is needed")
        if not self.security_requirements:
            warnings.append("security_requirements is empty — add auth, input validation, etc.")
        return warnings

    def to_checklist(self) -> str:
        """Format the plan as a markdown checklist the coder can work through."""
        lines: list[str] = ["## Architecture Plan Checklist\n"]

        if self.summary:
            lines.append(f"**Goal:** {self.summary}\n")

        if self.critical_rules:
            lines.append("### Critical Rules (NEVER violate)")
            for rule in self.critical_rules:
                lines.append(f"- {rule}")
            lines.append("")

        if self.tech_stack:
            lines.append("### Tech Stack")
            for item in self.tech_stack:
                v = f" {item.version}" if item.version else ""
                lines.append(f"- [ ] {item.name}{v} — {item.role}")
            lines.append("")

        if self.file_structure:
            lines.append("### File Structure (create these files)")
            for f in self.file_structure:
                lines.append(f"- [ ] `{f.path}` — {f.purpose} (max {f.max_lines} lines)")
            lines.append("")

        if self.database_tables:
            lines.append("### Database Tables")
            for t in self.database_tables:
                lines.append(f"- [ ] **{t.name}**")
                for col in t.columns:
                    lines.append(f"  - {col}")
                for rel in t.relationships:
                    lines.append(f"  - FK: {rel}")
            lines.append("")

        if self.api_endpoints:
            lines.append("### API Endpoints")
            for ep in self.api_endpoints:
                auth = " [auth]" if ep.auth_required else ""
                lines.append(f"- [ ] `{ep.method} {ep.path}`{auth} — {ep.purpose}")
            lines.append("")

        if self.components:
            lines.append("### Components")
            for c in self.components:
                lines.append(f"- [ ] **{c.name}** in `{c.file_path}` — {c.purpose}")
            lines.append("")

        if self.security_requirements:
            lines.append("### Security Requirements")
            for s in self.security_requirements:
                lines.append(f"- [ ] [{s.category}] {s.description}")
                if s.implementation_notes:
                    lines.append(f"  - Implementation: {s.implementation_notes}")
            lines.append("")

        if self.business_rules:
            lines.append("### Key Business Logic")
            for b in self.business_rules:
                src = f" (from {b.source_module})" if b.source_module else ""
                lines.append(f"- [ ] **{b.name}**{src}: {b.description}")
            lines.append("")

        if self.accessibility_notes:
            lines.append("### Accessibility")
            for note in self.accessibility_notes:
                lines.append(f"- [ ] {note}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON schema for injection into the architect prompt
# ---------------------------------------------------------------------------

PLAN_JSON_SCHEMA = """{
  "project_name": "string",
  "summary": "1-3 sentence overview",
  "tech_stack": [{"name": "string", "version": "string", "role": "string"}],
  "database_tables": [{"name": "string", "columns": ["string"], "relationships": ["string"], "indexes": ["string"]}],
  "api_endpoints": [{"method": "GET|POST|PATCH|DELETE", "path": "string", "purpose": "string", "request_shape": "string", "response_shape": "string", "auth_required": true, "middleware": ["string"]}],
  "components": [{"name": "string", "file_path": "string", "purpose": "string", "children": ["string"], "max_lines": 300}],
  "file_structure": [{"path": "string", "purpose": "string", "max_lines": 300}],
  "security_requirements": [{"category": "string", "description": "string", "implementation_notes": "string"}],
  "business_rules": [{"name": "string", "description": "string", "source_module": "string"}],
  "accessibility_notes": ["string"],
  "critical_rules": ["string"]
}"""


# ---------------------------------------------------------------------------
# Parse helpers
# ---------------------------------------------------------------------------

def parse_plan_from_text(text: str) -> ArchitecturePlan | None:
    """Extract and parse a JSON architecture plan from LLM output.

    Returns None if parsing fails.
    """
    # Try to find a ```json ... ``` block first
    match = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    raw = match.group(1) if match else None

    # Fallback: try the entire text as JSON
    if raw is None:
        # Look for a top-level { ... } object
        brace_match = re.search(r"\{.*\}", text, re.DOTALL)
        if brace_match:
            raw = brace_match.group(0)

    if raw is None:
        logger.warning("No JSON block found in architect output")
        return None

    try:
        data = json.loads(raw)
        plan = ArchitecturePlan.model_validate(data)
        warnings = plan.validate_completeness()
        for w in warnings:
            logger.warning(f"Plan incomplete: {w}")
        return plan
    except Exception as e:
        logger.warning(f"Failed to parse architecture plan JSON: {e}")
        return None
