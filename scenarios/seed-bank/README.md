# Bounded synthetic profile seed bank

[manifest.json](manifest.json) records the actual private preparation. 8,000 public source rows yielded 2,703 deduplicated eligible adult creative-context records, including 1,515 USA and 1,188 India records. 5,297 were rejected. The normalized bank is 17,411,259 bytes and satisfies the exact core 204 `tryfolk.persona-seeds.v1` schema. Its bytes remain in the private handoff, outside git, pending root's safety review before publication.

These are synthetic seeds for saved profile generation, not real customers, payment conditions, observed problems or exercised coverage. The catalog supplies the hypothetical purchase goal. Explicit source art/design/illustration/photography context supports a plain selection rationale; it does not prove purchase intent. Thousands of seeds do not expand the accepted N or either run budget.

## Sources and changes

NVIDIA Corporation publishes [Nemotron-Personas-USA](https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA) and [Nemotron-Personas-India](https://huggingface.co/datasets/nvidia/Nemotron-Personas-India) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Both pinned publisher cards state commercial use. Exact revisions and card hashes are in the manifest. Each normalized record retains dataset, revision, source UUID, row index, canonical original row checksum, normalized context checksum and attribution. The bundle revision is explicitly a checksum of the listed upstream source revisions; it is not an invented upstream git commit.

Tryfolk changes consist of adult/quality/relevance filtering, deduplication, field selection, Unicode and whitespace normalization, creative relevance tags and source evidence. Names inside source narrative are synthetic according to the publisher. Demographic and geographic columns are omitted from profile context; narrative may still mention names, places or ages. No NVIDIA endorsement is implied. Attribution, source links, license and change notices travel with the bank and each seed.

Tencent [PersonaHub](https://huggingface.co/datasets/proj-persona/PersonaHub) was excluded. Its pinned card declares CC BY-NC-SA 4.0, and the linked [official Tencent README](https://github.com/tencent-ailab/persona-hub) restricts released data to research purposes. No PersonaHub rows entered this bank, and no upstream synthesis code was downloaded or executed.

## Admission and selection limits

[normalize.py](normalize.py) admits only pinned sources with matching observed revision, positive adult age between 18 and 100, consistent source country, sufficient context and explicit visual creative terms. It rejects empty/thin/oversized/truncated context, self-description age contradictions, contact/link content and lexical instruction or credential patterns. Duplicate source IDs and normalized contexts cannot enter twice. The report retains rejection reasons and source IDs without rejected prose.

This is deterministic screening, not a guarantee against every semantic contradiction, stereotype, obfuscated injection or unrealistic persona. The first 4,000 rows per source form a bounded convenience sample, not a representative survey. No claim of statistical coverage, thousands of useful executed customers, fraud prediction or payment behavior follows from the accepted count.

`supported_context` is an explicit synthetic fixture overlay derived from runnable catalog entries. It does not infer US billing from source country, device from occupation or provider method from persona text. Core must separately match real registered target capabilities and conditions before accepting an assignment. Unregistered provider controls and unsupported cases stay excluded. The current overlay is the existing success-card/US/desktop/English/new-account condition; this bank does not establish Indian checkout coverage.

Core freezes scenario, seed, conditions and quote at acceptance. It creates the actual TinyTroupe profile and initial state/memory once before the first journey, then restores those saved values on the after pass. A bank update cannot regenerate an accepted population. UI renders core's saved context, rationale and source evidence; it does not own a second seed list or planner.

## Local preparation

Python 3.9 or later and the standard library are sufficient. From the repository root, use an empty private task directory for the anonymous bounded public download. It makes no provider, model, account or paid API calls:

```sh
python3 dodo-target/scenarios/seed-bank/fetch.py --output /private/task/public-seeds
python3 dodo-target/scenarios/seed-bank/normalize.py \
  --pages /private/task/public-seeds/pages \
  --catalog dodo-target/scenarios/payment-catalog.json \
  --output /private/task/seed-bank.json \
  --report /private/task/quality-report.json
python3 -m unittest discover -s dodo-target/scenarios/seed-bank -p test_normalize.py
```

Replace the example paths with an authorized private task location. Fetching preserves pinned card and raw response bytes; normalization is offline and refuses fewer than 2,000 eligible records or a bank above 32 MiB. The delivered preparation used the same pinned row endpoint and recorded response checksums plus parsed row envelopes. Its original HTTP response bytes were retained only for the initial preview; canonical row/envelope checksums identify the preserved data. Root's private handoff names the actual bank, report and sample paths. No service activation, import into a retained runtime or publication occurred.
