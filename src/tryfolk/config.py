from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass(frozen=True)
class Target:
    url: str
    revision: str
    fault: str = "healthy"
    provider: str = "local-fixture"
    repo_path: str | None = None
    allowed_origins: tuple[str, ...] = ()
    setup_gates: tuple[dict, ...] = ()
    source_commit: str | None = None
    source_digest: str | None = None
    mode: str | None = None
    execution_ready: bool = False
    target_provenance: dict | None = None
    scenario_capabilities: dict | None = None
    scenario_conditions: dict | None = None
    scenario_fixture_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Settings:
    db_path: Path = Path("runtime/tryfolk.sqlite")
    budget_microusd: int = 1_000_000
    owner_token: str | None = None
    max_personas: int = 20
    bulk_execution: bool = False
    browser_concurrency: int = 2
    per_persona_microusd: int = 20_000
    overhead_microusd: int = 50_000
    cfo_plan_path: Path | None = None
    targets: tuple[Target, ...] = ()
    scenario_catalog_path: Path | None = None
    seed_bank_path: Path | None = None
    seed_bank_min_records: int = 2000
    gates: tuple[dict, ...] = field(
        default_factory=lambda: (
            {
                "code": "sdk_validation",
                "message": "Codex SDK bridges need live validation.",
                "action": "Run the local integration probe; register its evidence.",
            },
        )
    )
    baseline_retry_approval_path: Path | None = None
    checkpoint_repository: str | None = None
    checkpoint_evidence_root: Path = field(
        default_factory=lambda: Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    )

    def __post_init__(self):
        if type(self.bulk_execution) is not bool:
            raise ValueError("TRYFOLK_BULK_EXECUTION must be true or false.")
        if type(self.browser_concurrency) is not int or not 1 <= self.browser_concurrency <= 10:
            raise ValueError("TRYFOLK_BROWSER_CONCURRENCY must be a whole number from 1 to 10.")
        if type(self.max_personas) is not int or not 1 <= self.max_personas <= 1000:
            raise ValueError("TRYFOLK_MAX_PERSONAS must be a whole number from 1 to 1000.")

    @classmethod
    def from_env(cls):
        import json

        max_personas = os.environ.get("TRYFOLK_MAX_PERSONAS", "20")
        if not max_personas.isascii() or not max_personas.isdecimal():
            raise ValueError("TRYFOLK_MAX_PERSONAS must be a whole number from 1 to 1000.")
        bulk_execution = os.environ.get("TRYFOLK_BULK_EXECUTION", "false")
        concurrency = os.environ.get("TRYFOLK_BROWSER_CONCURRENCY", "2")
        if bulk_execution not in ("true", "false"):
            raise ValueError("TRYFOLK_BULK_EXECUTION must be true or false.")
        if not concurrency.isascii() or not concurrency.isdecimal():
            raise ValueError("TRYFOLK_BROWSER_CONCURRENCY must be a whole number from 1 to 10.")
        connection_file = Path(os.environ.get("TRYFOLK_CONNECTIONS", "runtime/connections.json"))
        targets = (
            tuple(Target(**x) for x in json.loads(connection_file.read_text()))
            if connection_file.exists()
            else ()
        )
        validation = Path("runtime/sdk-validation.json")
        validated = False
        if validation.exists():
            proof = json.loads(validation.read_text())
            evidence = Path(proof.get("evidence", ""))
            pins = {
                "browser_use": "0.13.11",
                "stagehand": "4.2.0",
                "tinytroupe": "a6244b358a1fe1c71bf751f7ba0f8dfa368ec5a4",
            }
            if (
                proof.get("status") == "passed"
                and proof.get("model") == "gpt-6.1-sol"
                and proof.get("sdk_versions") == pins
                and evidence.is_file()
            ):
                result = json.loads(evidence.read_text())
                validated = (
                    result.get("phase") == "completed" and result.get("counts", {}).get("executed", 0) > 0
                )
        return cls(
            db_path=Path(os.environ.get("TRYFOLK_DB", "runtime/tryfolk.sqlite")),
            budget_microusd=int(os.environ.get("TRYFOLK_BUDGET_MICROUSD", "1000000")),
            owner_token=os.environ.get("TRYFOLK_OWNER_TOKEN"),
            max_personas=int(max_personas),
            bulk_execution=bulk_execution == "true",
            browser_concurrency=int(concurrency),
            baseline_retry_approval_path=Path(os.environ["TRYFOLK_BASELINE_RETRY_APPROVAL"])
            if os.environ.get("TRYFOLK_BASELINE_RETRY_APPROVAL")
            else None,
            checkpoint_repository=os.environ.get("TRYFOLK_CHECKPOINT_REPOSITORY"),
            targets=targets,
            scenario_catalog_path=Path(os.environ["TRYFOLK_SCENARIO_CATALOG"])
            if os.environ.get("TRYFOLK_SCENARIO_CATALOG")
            else None,
            seed_bank_path=Path(os.environ["TRYFOLK_SEED_BANK"])
            if os.environ.get("TRYFOLK_SEED_BANK")
            else None,
            cfo_plan_path=Path(os.environ["TRYFOLK_CFO_PLAN"])
            if os.environ.get("TRYFOLK_CFO_PLAN")
            else None,
            **({"gates": ()} if validated else {}),
        )
