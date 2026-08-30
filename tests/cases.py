"""Retrieval quality cases — the evidence for *why the search has to be hybrid*.

Each case is a real query we discussed while building the system. Together they show that no
single mechanism is enough: some queries are only found by the lexical channel, some only by the
semantic channel, some need the deterministic exact-match pin, some need the LLM pre-process to
be understood at all, and some need the cross-encoder rerank (see the differential tests in
`test_search.py` for those). SNOMED concept ids are stable across releases; ranks are not, so the
assertions use *bounds* (top-k / rank ≤ N) and the `channel` badge, never an exact position.

`expect` keys:
    concept              SCTID that must be retrieved (string)
    rank_le              its rank must be ≤ this (1 = must be first)
    top_k                it must appear within the first k results (default: len(results))
    channel              its fusion badge must be one of these ({"lexical","semantic","both"})
    is_exact             it must carry the exact-match flag
    expansion_contains   the gemma expansion must contain this substring (case-insensitive)
"""

# Top-level hierarchies used as descendant filters. In a real EHR the clinician usually knows the
# kind of concept they want (a finding vs a procedure), and constraining to it removes cross-domain
# noise (substances, qualifier values, organisms) — which sharpens the ranking of the target.
CLINICAL_FINDING = 404684003
PROCEDURE = 71388002

# Single-run cases: one search() call, asserted against `expect`.
SINGLE_CASES = [
    # ── Lexical channel is indispensable ────────────────────────────────────────────────────
    # A clinician's abbreviation. As a phrase "inf myo" embeds to noise (Miotic, Miso, megajoule),
    # so the semantic channel is useless here — only multi-prefix lexical matching recovers it.
    {
        "id": "lexical__inf_myo",
        "query": "inf myo", "gemma": False, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "abbreviation: 'inf'+'myo' prefixes → Myocardial infarction. Semantic embeds it as "
               "noise; the lexical channel finds it, and the Clinical finding filter lifts it to #1 "
               "by dropping substances/qualifiers/procedures.",
        "expect": {"concept": "22298006", "rank_le": 1, "channel": {"lexical", "both"}},
    },
    # Order-independence: same result with the two prefixes swapped.
    {
        "id": "lexical__order_independent",
        "query": "myo inf", "gemma": False, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "prefix order does not matter — 'myo inf' still reaches Myocardial infarction "
               "(top-3; 'inf myo'/'myo inf' are inherently ambiguous abbreviations).",
        "expect": {"concept": "22298006", "rank_le": 3, "channel": {"lexical", "both"}},
    },

    # Concise canonical term must win the lexical ranking: with prefix queries a verbose term can
    # accrue more matches (e.g. 'myo:*' hitting two words), so ts_rank uses word-count normalization
    # (flag 1|8) to keep the short canonical concept on top of its longer variants.
    {
        "id": "lexical__concise_canonical_wins",
        "query": "myocard infarct", "gemma": False, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "concise canonical 'Myocardial infarction' outranks its verbose variants "
               "(Acute/Electrocardiographic/…) — word-count normalization in ts_rank(…,1|8).",
        "expect": {"concept": "22298006", "rank_le": 1},
    },

    # ── Semantic channel is indispensable ───────────────────────────────────────────────────
    # Zero lexical overlap with "Renal pain": no shared token, so lexical returns nothing useful;
    # BioLORD bridges the lay phrasing to the clinical concept.
    {
        "id": "semantic__painful_kidney",
        "query": "painful kidney", "gemma": False, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "lay phrasing with no lexical overlap → Renal pain. Only the semantic channel bridges it.",
        "expect": {"concept": "274279008", "rank_le": 1, "channel": {"semantic", "both"}},
    },
    # Idiomatic English ("water on the knee") → knee joint effusion, again purely semantic.
    {
        "id": "semantic__water_on_the_knee",
        "query": "water on the knee", "gemma": False, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "idiom → Effusion of knee joint (top-3; several near-synonymous effusion findings rank "
               "alongside it). No lexical overlap; semantic only.",
        "expect": {"concept": "202381003", "rank_le": 3, "channel": {"semantic", "both"}},
    },

    # ── Exact-match pin ─────────────────────────────────────────────────────────────────────
    # "hepatomegaly" is a synonym of "Large liver". The deterministic exact_first rule keeps it at
    # the very top, ahead of more specific siblings (Congenital/Schistosomal hepatomegaly, …).
    {
        "id": "exact__hepatomegaly",
        "query": "hepatomegaly", "gemma": False, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "exact synonym of 'Large liver' → pinned at rank 1 by exact_first, above its subtypes.",
        "expect": {"concept": "80515008", "rank_le": 1, "is_exact": True},
    },

    # ── LLM pre-process (translation / abbreviation expansion) ───────────────────────────────
    # Spanish lay term. Without translation the English index can't be reached reliably; gemma maps
    # "presion alta" → hypertension, which then matches exactly.
    {
        "id": "gemma__presion_alta_es",
        "query": "presion alta", "gemma": True, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "Spanish lay term → gemma translates to 'hypertension' → Hypertensive disorder (exact).",
        "expect": {"concept": "38341003", "rank_le": 1, "expansion_contains": "hypertens"},
    },
    # Spanish acronym EPOC → COPD.
    {
        "id": "gemma__epoc_acronym",
        "query": "EPOC", "gemma": True, "rerank": False, "filter": CLINICAL_FINDING,
        "why": "Spanish acronym → gemma expands to 'chronic obstructive pulmonary disease' (exact).",
        "expect": {"concept": "13645005", "rank_le": 1, "expansion_contains": "obstructive"},
    },
    # Spanish phrase → canonical English term feeds both channels.
    {
        "id": "gemma__radiografia_de_torax_es",
        "query": "radiografia de torax", "gemma": True, "rerank": False, "filter": PROCEDURE,
        "why": "Spanish phrase → gemma normalizes to 'chest x-ray' → Plain X-ray of chest; the "
               "Procedure filter drops the qualifier-value radiology concepts and lifts it to #1.",
        "expect": {"concept": "399208008", "rank_le": 1, "expansion_contains": "x-ray"},
    },
]

# Concepts referenced by the differential tests (kept here so the collection is self-documenting).
PLAIN_XRAY_CHEST = "399208008"      # a Procedure — the precise answer for "chest x-ray"
THORACIC_RADIOLOGY = "1304212007"   # a Qualifier value — generic, and NOT under Procedure
PROCEDURE_ROOT = 71388002           # 71388002 | Procedure (SNOMED top-level)
