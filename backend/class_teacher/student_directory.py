from __future__ import annotations

from contextlib import closing
from typing import Callable

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .secure_repository import EncryptedObjectRepository


class StudentDirectory:
    """Read only identity metadata and aggregate counts for the roster."""

    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider

    def search(
        self,
        *,
        token: str,
        q: str | None = None,
        class_label: str | None = None,
        state: str | None = None,
        roster_state: str | None = None,
        sort: str = "last_confirmed_desc",
        cursor: str | None = None,
        page_size: int = 20,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        size = max(1, min(int(page_size), 50))
        offset = max(0, int(cursor or "0")) if str(cursor or "0").isdigit() else 0
        needle = str(q or "").strip().casefold()
        requested_class = str(class_label or "").strip()
        requested_state = str(state or "").strip()
        requested_roster_state = str(roster_state or "").strip()
        with closing(self.database.connect()) as connection:
            rows = connection.execute(
                """
                SELECT s.subject_id, s.payload_object_id, s.state, s.updated_at,
                       (SELECT COUNT(*) FROM support_records r
                        WHERE r.subject_id = s.subject_id AND r.state = 'active') AS support_record_count,
                       (SELECT COUNT(*) FROM support_plans p
                        WHERE p.subject_id = s.subject_id AND p.state = 'active') AS support_plan_count,
                       (SELECT COUNT(*) FROM attention_cards a
                        WHERE a.subject_id = s.subject_id AND a.state = 'draft') AS attention_pending_count,
                       (SELECT COUNT(*) FROM student_card_entries c
                        WHERE c.subject_id = s.subject_id AND c.state = 'active') AS confirmed_entry_count,
                       (SELECT COUNT(*) FROM affair_student_links l
                        WHERE l.subject_id = s.subject_id) AS affair_count,
                       COALESCE((SELECT m.state FROM class_roster_memberships m
                                 WHERE m.subject_id=s.subject_id), 'manual') AS roster_state,
                       (SELECT MAX(created_at) FROM student_card_entries c
                        WHERE c.subject_id = s.subject_id AND c.state = 'active') AS last_confirmed_at,
                       (SELECT CASE WHEN COUNT(*) = 0 THEN 'none'
                            WHEN SUM(CASE WHEN o.state = 'pending' THEN 1 ELSE 0 END) > 0 THEN 'pending'
                            ELSE 'applied' END
                        FROM sensitive_work_groups g
                        LEFT JOIN sensitive_work_projection_outbox o ON o.group_id = g.group_id
                        WHERE g.source_kind = 'student_support'
                          AND g.source_id IN (
                              SELECT entry_id FROM student_card_entries
                              WHERE subject_id = s.subject_id
                          )) AS projection_state
                FROM student_subject_links s
                ORDER BY s.updated_at DESC, s.subject_id
                """
            ).fetchall()
            items = []
            for row in rows:
                identity, _revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                item = {
                    "subject_id": str(row["subject_id"]),
                    "source_student_id": str(identity.get("source_student_id") or ""),
                    "display_name": str(identity.get("display_name") or ""),
                    "class_label": str(identity.get("class_label") or ""),
                    "confirmed_entry_count": int(row["confirmed_entry_count"]),
                    "affair_count": int(row["affair_count"]),
                    "support_record_count": int(row["support_record_count"]),
                    "support_plan_count": int(row["support_plan_count"]),
                    "attention_pending_count": int(row["attention_pending_count"]),
                    "projection_state": str(row["projection_state"] or "none"),
                    "last_confirmed_at": (
                        None if row["last_confirmed_at"] is None else str(row["last_confirmed_at"])
                    ),
                    "state": str(row["state"]),
                    "roster_state": str(row["roster_state"]),
                }
                haystack = f"{item['display_name']} {item['source_student_id']}".casefold()
                if needle and needle not in haystack:
                    continue
                if requested_class and item["class_label"] != requested_class:
                    continue
                if requested_state and item["state"] != requested_state:
                    continue
                if requested_roster_state and item["roster_state"] != requested_roster_state:
                    continue
                items.append(item)
        if sort == "name_asc":
            items.sort(key=lambda item: (str(item["display_name"]), str(item["subject_id"])))
        elif sort == "records_desc":
            items.sort(key=lambda item: (-int(item["support_record_count"]), str(item["display_name"])))
        elif sort == "attention_desc":
            items.sort(key=lambda item: (-int(item["attention_pending_count"]), str(item["display_name"])))
        elif sort == "last_confirmed_desc":
            items.sort(key=lambda item: (str(item["last_confirmed_at"] or ""), str(item["display_name"])), reverse=True)
        else:
            raise VaultError("student_directory_sort_invalid", "学生名单排序无效", status_code=422)
        page = items[offset:offset + size]
        next_cursor = str(offset + size) if offset + size < len(items) else None
        return {
            "items": page,
            "total": len(items),
            "cursor": next_cursor,
            "page_size": size,
        }

    def open(self, *, token: str, subject_id: str) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT s.payload_object_id,
                  (SELECT COUNT(*) FROM support_records r WHERE r.subject_id=s.subject_id AND r.state='active') support_record_count,
                  (SELECT COUNT(*) FROM support_plans p WHERE p.subject_id=s.subject_id AND p.state='active') support_plan_count,
                  (SELECT COUNT(*) FROM attention_cards a WHERE a.subject_id=s.subject_id AND a.state='draft') attention_pending_count,
                  (SELECT COUNT(*) FROM student_card_entries c WHERE c.subject_id=s.subject_id AND c.state='active') confirmed_entry_count,
                  (SELECT COUNT(*) FROM affair_student_links l WHERE l.subject_id=s.subject_id) affair_count,
                  (SELECT MAX(created_at) FROM student_card_entries c WHERE c.subject_id=s.subject_id AND c.state='active') last_confirmed_at
                FROM student_subject_links s WHERE s.subject_id = ?
                """,
                (subject_id,),
            ).fetchone()
            if row is None:
                raise VaultError("support_subject_not_found", "学生档案不存在", status_code=404)
            identity, _revision = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(row["payload_object_id"]),
            )
            affair_rows = connection.execute(
                """
                SELECT a.affair_id, a.payload_object_id, a.state, a.updated_at
                FROM affair_student_links l
                JOIN affairs a ON a.affair_id=l.affair_id
                WHERE l.subject_id=?
                ORDER BY a.updated_at DESC, a.affair_id
                """,
                (subject_id,),
            ).fetchall()
            related_affairs = []
            for affair in affair_rows:
                protected, _ = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(affair["payload_object_id"]),
                )
                related_affairs.append(
                    {
                        "affair_id": str(affair["affair_id"]),
                        "title": str(protected.get("title") or ""),
                        "summary": protected.get("summary"),
                        "state": str(affair["state"]),
                        "updated_at": str(affair["updated_at"]),
                    }
                )
        return {
            "subject_id": subject_id,
            "source_student_id": str(identity.get("source_student_id") or ""),
            "display_name": str(identity.get("display_name") or ""),
            "class_label": str(identity.get("class_label") or ""),
            "support_record_count": int(row["support_record_count"]),
            "support_plan_count": int(row["support_plan_count"]),
            "attention_pending_count": int(row["attention_pending_count"]),
            "confirmed_entry_count": int(row["confirmed_entry_count"]),
            "affair_count": int(row["affair_count"]),
            "related_affairs": related_affairs,
            "projection_state": "unknown",
            "last_confirmed_at": None if row["last_confirmed_at"] is None else str(row["last_confirmed_at"]),
        }


__all__ = ["StudentDirectory"]
