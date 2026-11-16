# Memory Duplicate Inspection

Yuki can inspect proposed memory text for possible duplicates before saving it.

## Behavior

- Inspection is scoped to the authenticated owner's active memories.
- Expired entries are excluded.
- Exact matches use Unicode NFKC normalization, case folding, and whitespace/punctuation normalization.
- Similar candidates use conservative token-set Jaccard similarity; the default threshold is 0.8 and callers may choose 0.5–1.0.
- Results include the existing memory ID, category, expiry, exact-match flag, and similarity score.
- Inspection is advisory. It does not prevent a save, overwrite an existing memory, merge categories, change retention, or resolve contradictory facts.
- Different categories are surfaced rather than silently reconciled. The caller remains responsible for asking the owner which item to keep.

The feature deliberately favors avoiding destructive automatic deduplication over aggressive matching. Lexical similarity can miss paraphrases and can produce false positives, so candidates must be reviewed before any deletion or replacement.
