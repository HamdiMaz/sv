# Index duplicate skill handling

## Goal

When `sv index` scans a repository, duplicated skill folders can make `.sv/index.toml` noisy. The index should keep one canonical entry for identical skill content while preserving distinct skills that only share a name.

## Behavior

- `sv index` scans and validates candidate `SKILL.md` files as it does today.
- After hashing valid candidates, entries are deduplicated by the pair `(content_hash, skill_file_hash)`.
- If multiple entries have the same pair, `sv index` keeps the entry whose `source_path` is closest to the repository root.
- If duplicate entries have the same path depth, `sv index` keeps the lexicographically smallest `source_path` for deterministic output.
- Skills with the same `name` but different hashes remain in the index, so existing path-aware duplicate-name behavior still works.
- Hash duplicates are skipped silently; the cleaner index is the user-visible result.

## Implementation notes

Add a pure helper in `src/sv/index.py` that accepts scanned `IndexSkillEntry` values and returns the deduplicated list. `scan_repo_for_index` should call this helper after collecting entries and before sorting/writing the document. Tests should cover shallow-path preference, deterministic tie-breaking, and preserving same-name entries with different content.
