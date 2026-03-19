"""Base classes for the Skills framework.

Every skill extends the Skill base class and implements execute().
The engine handles orchestration, logging, and error recovery.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from enum import Enum


class StepStatus(Enum):
    """Status of an individual skill step."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepResult:
    """Result of a single step within a skill execution."""
    step_name: str
    status: StepStatus
    output: Any = None
    error: Optional[str] = None
    duration_ms: int = 0


@dataclass
class SkillResult:
    """Final result of a complete skill execution."""
    skill_name: str
    success: bool
    steps: List[StepResult] = field(default_factory=list)
    final_output: Any = None
    error: Optional[str] = None

    def summary(self) -> str:
        """Human-readable summary for MCP tool response."""
        lines = [f"Skill: {self.skill_name}"]
        lines.append("=" * 40)
        for s in self.steps:
            if s.status == StepStatus.SUCCESS:
                icon = "OK"
            elif s.status == StepStatus.FAILED:
                icon = "FAIL"
            elif s.status == StepStatus.SKIPPED:
                icon = "SKIP"
            else:
                icon = "..."
            lines.append(f"  [{icon}] {s.step_name}")
            if s.output and s.status == StepStatus.SUCCESS:
                # Truncate long outputs
                out_str = str(s.output)[:300]
                lines.append(f"         {out_str}")
            if s.error:
                lines.append(f"         Error: {s.error}")
        lines.append("=" * 40)
        if self.success and self.final_output:
            lines.append(f"RESULT: {self.final_output}")
        elif self.error:
            lines.append(f"FAILED: {self.error}")
        return "\n".join(lines)


class Skill:
    """Base class for all skills.

    Subclasses must define:
        - name: unique identifier (used as MCP tool name)
        - description: shown to the model in tool listing
        - input_schema: JSON Schema for the skill's parameters
        - execute(): the orchestration logic

    Subclasses may define:
        - blocked_imports: libraries blocked in execute_code
        - max_code_lines: per-call limit for execute_code
        - max_stdout_chars: stdout cap for execute_code
        - save_path_template: where output files go
    """

    name: str = ""
    description: str = ""
    input_schema: dict = {}

    # Guardrails (overridable per-skill)
    blocked_imports: List[str] = []
    blocked_patterns: List[str] = []
    max_code_lines: int = 50
    max_stdout_chars: int = 5000
    save_path_template: str = "/app/sandbox_data/{session_name}/"

    async def execute(
        self, params: Dict[str, Any], ctx: "SkillContext"
    ) -> SkillResult:
        """Execute the skill workflow. Must be overridden."""
        raise NotImplementedError(f"Skill {self.name} must implement execute()")

    def validate_params(self, params: Dict[str, Any]) -> Optional[str]:
        """Validate input params. Return error message or None."""
        return None
