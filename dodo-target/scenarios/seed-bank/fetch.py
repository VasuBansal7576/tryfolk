"""Bounded anonymous public data download. No provider, model or account calls."""
import argparse
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

from normalize import PINNED_SOURCES, canonical


def fetch_page(job, directory):
    dataset, source, offset = job
    query = urllib.parse.urlencode({
        "dataset": dataset, "config": "default", "split": source["split"],
        "revision": source["revision"], "offset": offset, "length": 100,
    })
    url = "https://datasets-server.huggingface.co/rows?" + query
    with urllib.request.urlopen(url, timeout=25) as response:
        revision = response.headers.get("x-revision")
        body = response.read(2_000_001)
    if revision != source["revision"]:
        raise ValueError("Public dataset revision changed; do not import this page.")
    if len(body) > 2_000_000:
        raise ValueError("Public page exceeded the 2 MB bound.")
    data = json.loads(body)
    if data.get("partial") or len(data.get("rows", [])) != 100:
        raise ValueError("Public page is incomplete.")
    name = dataset.rsplit("/", 1)[-1] + "-" + str(offset).zfill(5)
    raw = directory / (name + ".response")
    raw.write_bytes(body)
    evidence = {
        "dataset": dataset, "revision": source["revision"], "observed_revision": revision,
        "split": source["split"], "offset": offset, "length": 100,
        "request_url": url, "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_bytes": len(body), "raw_response_file": raw.name,
    }
    (directory / (name + ".json")).write_text(canonical({"source": evidence, "data": data}) + "\n", encoding="utf-8")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Private task directory")
    parser.add_argument("--rows-per-source", type=int, default=4000)
    args = parser.parse_args()
    if args.rows_per_source < 100 or args.rows_per_source > 4000 or args.rows_per_source % 100:
        parser.error("rows-per-source must be a multiple of 100 between 100 and 4000")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("output must be empty; preserve prior immutable download evidence")
    args.output.mkdir(parents=True, exist_ok=True)
    cards = []
    for dataset, source in sorted(PINNED_SOURCES.items()):
        url = "https://huggingface.co/datasets/" + dataset + "/resolve/" + source["revision"] + "/README.md"
        with urllib.request.urlopen(url, timeout=25) as response:
            card = response.read(500_001)
        text = card.decode("utf-8")
        if len(card) > 500_000 or "license: cc-by-4.0" not in text or "commercial" not in text.lower():
            raise ValueError("Pinned source card does not satisfy the reviewed data license boundary.")
        name = dataset.rsplit("/", 1)[-1] + "-README.md"
        (args.output / name).write_bytes(card)
        cards.append({"dataset": dataset, "revision": source["revision"], "url": url,
                      "card_sha256": hashlib.sha256(card).hexdigest(), "file": name})
    pages_directory = args.output / "pages"
    pages_directory.mkdir()
    jobs = [(dataset, source, offset) for dataset, source in sorted(PINNED_SOURCES.items())
            for offset in range(0, args.rows_per_source, 100)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(lambda job: fetch_page(job, pages_directory), jobs))
    manifest = {"rows": sum(page["length"] for page in pages), "max_rows": 8000,
                "response_bytes": sum(page["body_bytes"] for page in pages),
                "source_cards": cards, "pages": pages, "provider_calls": 0, "model_calls": 0}
    (args.output / "download-manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
    print("Downloaded", manifest["rows"], "pinned public rows; no source code executed.")


if __name__ == "__main__":
    main()
