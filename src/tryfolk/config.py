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
    checkpoint_repository: str | None = None
    checkpoint_evidence_root: Path = field(
        default_factory=lambda: Path(os.environ.get("TRYFOLK_EVIDENCE_ROOT", "runtime/evidence"))
    )

    @classmethod
    def from_env(cls):
        import json

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
