"""Prepare public synthetic context locally. This module never executes seed text."""
import hashlib
import json
import re
import unicodedata
from collections import Counter

PINNED_SOURCES = {
    "nvidia/Nemotron-Personas-USA": {
        "revision": "5b4cd35ab46490c1da1bd2b5a2324d6f871be180", "country": "USA", "split": "train",
    },
    "nvidia/Nemotron-Personas-India": {
        "revision": "adefeefcc0fc3f85726d20bed0e4ed8d66372a54", "country": "India", "split": "en_IN",
    },
}
CONTEXT_FIELDS = {
    "professional": "professional_persona", "arts": "arts_persona",
    "skills": "skills_and_expertise", "interests": "hobbies_and_interests",
}
CREATIVE_TERMS = {
    "digital_art": r"\bdigital (?:art|illustration|painting)\b",
    "design_assets": r"\b(?:graphic design|vector graphics|layout design|visual design|design assets)\b",
    "illustration": r"\b(?:illustration|illustrations|illustrator)\b",
    "photography": r"\b(?:photography|photographer|photo editing)\b",
    "painting_drawing": r"\b(?:painting|drawing|watercolo[u]?r|sketching)\b",
    "visual_communication": r"\b(?:visual storytelling|visual communication|poster design|typography)\b",
}
INSTRUCTION_PATTERNS = [
    r"ignore\s+(?:(?:all|the|any)\s+)?(?:previous|prior|above|system)\s+(?:instructions|prompts|messages)",
    r"(?:reveal|disclose|print|override|bypass)\b.{0,60}\b(?:system prompt|credentials|secret|instructions)",
    r"\byou are\s+(?:chatgpt|an?\s+(?:ai|assistant|agent))\b",
    r"\b(?:system prompt|tool call|developer message|assistant instructions)\b",
    r"<\|(?:im_start|system|assistant|endoftext)|\[INST\]|```|<script\b",
    r"\b(?:curl|wget|eval|exec)\s*\(|\$\(|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----",
    r"\b(?:sk-[a-zA-Z0-9]{20,}|AKIA[A-Z0-9]{16})\b",
]


class Rejected(ValueError):
    """A source record cannot enter the local seed bank."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def clean(value):
    return " ".join(unicodedata.normalize("NFKC", value).split())


def normalize_row(row, *, dataset, revision, row_index):
    source = PINNED_SOURCES.get(dataset)
    if not source or revision != source["revision"]:
        raise Rejected("unapproved_source_revision")
    if not isinstance(row, dict) or not isinstance(row_index, int) or row_index < 0:
        raise Rejected("invalid_record")
    if not isinstance(row.get("uuid"), str) or not re.fullmatch(r"[a-f0-9]{32}", row["uuid"]):
        raise Rejected("invalid_source_id")
    age = row.get("age")
    if type(age) is not int or age > 100 or age < 0:
        raise Rejected("invalid_age")
    if age < 18:
        raise Rejected("not_adult")
    if row.get("country") != source["country"]:
        raise Rejected("country_mismatch")
    # Screen all source prose, including fields deliberately omitted from the output.
    all_text = " ".join(clean(value) for value in row.values() if isinstance(value, str))
    if any(re.search(pattern, all_text, re.IGNORECASE) for pattern in INSTRUCTION_PATTERNS):
        raise Rejected("instruction_like_content")
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}|https?://", all_text, re.IGNORECASE):
        raise Rejected("contact_or_link_content")
    if any(len(value) > 6000 for value in row.values() if isinstance(value, str)):
        raise Rejected("oversized_content")
    # Check self-description ages, not family-member or historical ages in other fields.
    self_text = " ".join(str(row.get(key, "")) for key in ("persona", "professional_persona"))
    ages = re.findall(r"\b(\d{1,3})[\s\-\u2011\u2013]*year[\s\-\u2011\u2013]*old\b", self_text, re.IGNORECASE)
    if any(int(value) != age for value in ages):
        raise Rejected("contradictory_age")
    context = {}
    for key, field in CONTEXT_FIELDS.items():
        value = row.get(field)
        if not isinstance(value, str) or len(clean(value)) < 30:
            raise Rejected("empty_context")
        context[key] = clean(value)
    if sum(map(len, context.values())) < 300:
        raise Rejected("thin_context")
    text = " ".join(context.values())
    tags = [tag for tag, pattern in CREATIVE_TERMS.items() if re.search(pattern, text, re.IGNORECASE)]
    if not tags:
        raise Rejected("no_explicit_creative_relevance")
    evidence = [
        {"field": field, "tag": tag, "matched_term": match.group(0)}
        for field, value in context.items()
        for tag, pattern in CREATIVE_TERMS.items()
        for match in [re.search(pattern, value, re.IGNORECASE)] if match
    ]
    fingerprint = sha256(context)
    return {
        "seed_id": "synthetic-" + fingerprint,
        "label": "synthetic_seed_profile",
        "adult_verified": True,
        "source_dataset": dataset,
        "source_revision": revision,
        "source_row_id": row["uuid"],
        "source_row_index": row_index,
        "source_row_sha256": sha256(row),
        "normalized_sha256": fingerprint,
        "context": context,
        "relevance_tags": tags,
        "relevance_evidence": evidence,
        "selection_rationale": "Explicit creative context supports a Special Downloads profile seed: " + ", ".join(tags) + ". The accepted scenario supplies the purchase goal. This text supplies no payment condition or predicted outcome.",
        "provider_conditions_established": False,
        "changes": [
            "NFKC Unicode and whitespace normalization.",
            "Retained professional/art/skills/interests context; omitted demographic, geographic and unrelated source fields.",
            "Added synthetic label, adult admission, source hashes and deterministic creative relevance tags. No inference calls or payment predictions.",
        ],
    }


def build_bank(pages, catalog, *, minimum=2000):
    """Admit data only. Core still checks real target registration before selection."""
    supported = {}
    candidate_ids = []
    for scenario in catalog.get("scenarios", []):
        if (scenario.get("availability") == "runnable"
                and scenario.get("check_kind") == "customer_journey"
                and scenario.get("condition_fixture", {}).get("registration") == "existing_driver"):
            conditions = scenario.get("required_capabilities", {}).get("conditions", {})
            if conditions and all(isinstance(key, str) and isinstance(value, str) and value for key, value in conditions.items()):
                candidate_ids.append(scenario["id"])
                for key, value in conditions.items():
                    supported.setdefault(key, set()).add(value)
    if not supported:
        raise Rejected("no_registered_catalog_conditions")
    supported_context = {key: sorted(values) for key, values in sorted(supported.items())}
    seeds, rejected = [], []
    reasons, accepted_sources, examined_sources = Counter(), Counter(), Counter()
    seen_source_ids, seen_context = set(), set()
    source_pages = []
    for page in sorted(pages, key=lambda item: (item["source"]["dataset"], item["source"].get("offset", 0))):
        source, data = page["source"], page["data"]
        expected = PINNED_SOURCES.get(source.get("dataset"))
        if (not expected or source.get("revision") != expected["revision"]
                or source.get("observed_revision") != expected["revision"]
                or source.get("split") != expected["split"]):
            raise Rejected("page_revision_mismatch")
        if data.get("partial"):
            raise Rejected("incomplete_page")
        source_pages.append(source)
        for item in data.get("rows", []):
            examined_sources[source["dataset"]] += 1
            if sum(examined_sources.values()) > 8000:
                raise Rejected("sample_bound_exceeded")
            try:
                if item.get("truncated_cells"):
                    raise Rejected("truncated_source_cells")
                seed = normalize_row(item["row"], dataset=source["dataset"], revision=source["revision"], row_index=item["row_idx"])
                identity = (seed["source_dataset"], seed["source_row_id"])
                context_key = canonical(seed["context"]).casefold()
                if identity in seen_source_ids:
                    raise Rejected("duplicate_source_id")
                if context_key in seen_context:
                    raise Rejected("duplicate_context")
                seen_source_ids.add(identity)
                seen_context.add(context_key)
                seed.update({
                    "id": seed["seed_id"],
                    "source_record_id": seed["source_row_id"],
                    "profile": {"synthetic_seed_profile": True, **seed["context"]},
                    "supported_context": supported_context,
                    "eligibility": {"adult": True, "commercial_use": True, "quality_passed": True},
                    "scenario_candidates": sorted(candidate_ids),
                    "context_basis": "Catalog-supported synthetic test fixture overlay, not a prediction from source country or demographics. Core must verify actual target registration before accepting an assignment.",
                    "source": {
                        "dataset": source["dataset"], "revision": source["revision"],
                        "url": "https://huggingface.co/datasets/" + source["dataset"],
                        "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
                        "attribution": "NVIDIA Corporation, " + source["dataset"] + "; bounded synthetic context adapted by Tryfolk. Changes listed in this record. No NVIDIA endorsement.",
                    },
                })
                seeds.append(seed)
                accepted_sources[source["dataset"]] += 1
            except Rejected as error:
                reason = str(error)
                reasons[reason] += 1
                rejected.append({"dataset": source["dataset"], "row_index": item.get("row_idx"),
                                 "source_row_id": item.get("row", {}).get("uuid"), "reason": reason})
    if len(seeds) < minimum:
        raise Rejected("insufficient_eligible_records: " + str(len(seeds)))
    upstream = [
        {"dataset": dataset, "revision": PINNED_SOURCES[dataset]["revision"],
         "url": "https://huggingface.co/datasets/" + dataset,
         "license": "CC-BY-4.0", "publisher": "NVIDIA Corporation"}
        for dataset in sorted(accepted_sources)
    ]
    source_revision = "sha256:" + sha256(upstream)
    bank = {
        "schema_version": "tryfolk.persona-seeds.v1",
        "version": "2026-10-11.normalize-v1." + sha256(seeds),
        "source": {
            "dataset": "NVIDIA Nemotron-Personas bounded USA/India bundle",
            "url": "https://huggingface.co/nvidia", "revision": source_revision,
            "revision_kind": "sha256_of_explicit_upstream_source_bundle_not_an_upstream_git_commit",
            "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "NVIDIA Corporation, Nemotron-Personas-USA and Nemotron-Personas-India. Adapted by Tryfolk through filtering, field selection, Unicode/whitespace normalization and relevance tagging. No NVIDIA endorsement.",
            "upstream_sources": upstream,
        },
        "seeds": seeds,
        "catalog_version": catalog.get("version", catalog.get("catalog_version")),
        "catalog_sha256": sha256(catalog),
        "synthetic_data_only": True,
        "root_publication_review_required": True,
        "provider_conditions_established": False,
        "quality_scope": "Deterministic admission checks only; no human realism validation, semantic contradiction proof, payment prediction or exercised coverage.",
    }
    report = {
        "examined": sum(examined_sources.values()), "accepted": len(seeds), "rejected": len(rejected),
        "examined_by_source": dict(sorted(examined_sources.items())),
        "accepted_by_source": dict(sorted(accepted_sources.items())),
        "rejected_by_reason": dict(sorted(reasons.items())), "rejected_records": rejected,
        "source_pages": source_pages, "bank_canonical_sha256": sha256(bank),
        "provider_calls": 0, "model_calls": 0, "personas_executed": 0,
    }
    if len(canonical(bank).encode("utf-8")) > 32 * 1024 * 1024:
        raise Rejected("import_size_bound_exceeded")
    return bank, report


def main():
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=Path, required=True, help="Local bounded pinned row envelopes, no network calls")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="Private normalized bank JSON")
    parser.add_argument("--report", type=Path, required=True, help="Private admission and provenance report")
    args = parser.parse_args()
    pages = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(args.pages.glob("*.json"))]
    bank, report = build_bank(pages, json.loads(args.catalog.read_text(encoding="utf-8")))
    for path, value in ((args.output, bank), (args.report, report)):
        path.write_text(canonical(value) + "\n", encoding="utf-8")
    print("Accepted", report["accepted"], "of", report["examined"], "synthetic records; executed personas 0.")


if __name__ == "__main__":
    main()
