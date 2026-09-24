from __future__ import annotations

import re
import sqlite3

from question_bank.relations.contracts import normalize_stable_key
from question_bank.taxonomy.registry import (
    CANONICAL_KNOWLEDGE,
    canonicalize_knowledge_exact,
)


def bootstrap_governed_knowledge_identities(
    connection: sqlite3.Connection,
    *,
    preserve_conflicts: bool = False,
) -> None:
    """
    Seed the governed catalog and map only exact historical knowledge tags.

    The caller owns the transaction. Unknown or ambiguous historical values are
    intentionally left unmapped so a later teacher-governance workflow can
    decide them without inventing stable identities.
    """

    conflicting_keys: set[str] = set()
    # Load the seeded catalog once; per-row existence checks multiply into
    # tens of thousands of statements on a warm database.
    existing_identities = {
        str(row[0]): (str(row[1]), str(row[2]))
        for row in connection.execute(
            "SELECT stable_key, display_name, origin FROM knowledge_tag_identities"
        ).fetchall()
    }
    existing_aliases = {
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT stable_key, normalized_alias FROM knowledge_tag_aliases"
        ).fetchall()
    }
    for item in CANONICAL_KNOWLEDGE:
        stable_key = normalize_stable_key(item.canonical_id)
        existing = existing_identities.get(stable_key)
        if existing is None:
            connection.execute(
                """
                INSERT INTO knowledge_tag_identities (
                    stable_key, display_name, origin
                ) VALUES (?, ?, 'builtin')
                """,
                (stable_key, item.canonical_name),
            )
            existing_identities[stable_key] = (item.canonical_name, "builtin")
        elif (
            str(existing[0]) != item.canonical_name
            or str(existing[1]) != "builtin"
        ):
            if preserve_conflicts:
                conflicting_keys.add(stable_key)
                continue
            raise ValueError(
                f"governed knowledge identity conflicts with {stable_key}"
            )

        aliases = (
            ("stable_key", item.canonical_id),
            ("canonical_name", item.canonical_name),
            *(("registered_alias", alias) for alias in item.aliases),
        )
        for alias_kind, alias in aliases:
            normalized_alias = normalize_knowledge_alias(alias)
            if not normalized_alias:
                continue
            # Skip the write entirely when the alias row already exists: an
            # ON CONFLICT DO NOTHING hit still opens a write transaction,
            # which touches the WAL file on every call.
            if (stable_key, normalized_alias) in existing_aliases:
                continue
            existing_aliases.add((stable_key, normalized_alias))
            connection.execute(
                """
                INSERT INTO knowledge_tag_aliases (
                    stable_key, alias, normalized_alias, alias_kind
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(stable_key, normalized_alias) DO NOTHING
                """,
                (stable_key, str(alias).strip(), normalized_alias, alias_kind),
            )

    historical_rows = connection.execute(
        """
        SELECT id, tag_value
        FROM question_tags
        WHERE tag_type = 'knowledge_point'
          AND TRIM(COALESCE(tag_value, '')) <> ''
        ORDER BY id
        """
    ).fetchall()
    mapped_tag_ids = {
        int(row[0])
        for row in connection.execute(
            "SELECT question_tag_id FROM knowledge_tag_identity_mappings"
        ).fetchall()
    }
    for row in historical_rows:
        if int(row[0]) in mapped_tag_ids:
            # The row identity is stable even if a teacher later edits the
            # visible tag text. Re-running bootstrap must not reinterpret that
            # edit as a move to a different governed identity.
            continue
        canonical = canonicalize_knowledge_exact(row[1])
        if canonical is None:
            continue
        stable_key = normalize_stable_key(canonical.canonical_id)
        if stable_key in conflicting_keys:
            continue
        connection.execute(
            """
            INSERT INTO knowledge_tag_identity_mappings (
                question_tag_id,
                stable_key,
                source_value_snapshot,
                mapping_source
            ) VALUES (?, ?, ?, 'governed_exact')
            """,
            (int(row[0]), stable_key, str(row[1]).strip()),
        )


def normalize_knowledge_alias(value: object) -> str:
    return re.sub(r"[\s\W_]+", "", str(value or "").strip()).casefold()


__all__ = [
    "bootstrap_governed_knowledge_identities",
    "normalize_knowledge_alias",
]
