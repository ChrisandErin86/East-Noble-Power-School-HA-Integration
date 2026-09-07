"""Todo platform for PowerSchool Grades & Attendance.

One read-only to-do list per student, mirroring their currently-missing
assignments (the same data behind the Missing Assignments sensor's
`assignments` attribute in sensor.py -- this just gives it a native list UI
instead of requiring a Jinja markdown card).

Deliberately read-only: this list is recomputed from scratch on every poll
(see coordinator.py / const.py for the default 45-minute interval) from
whatever PowerSchool currently reports missing. There's no "delete" or
"check off" that makes sense to send back upstream -- checking an assignment
off here wouldn't turn it in -- so no TodoListEntityFeature is declared.
Home Assistant's own to-do list card disables add/check-off controls when an
entity doesn't advertise support for them, which is exactly the behavior we
want: a display-only list that always mirrors the portal.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import MissingAssignment
from .const import DATA_COORDINATOR, DOMAIN
from .coordinator import PowerSchoolCoordinator
from .entity import PowerSchoolStudentEntity

# Seen from this district's missing-assignments page as MM/DD/YYYY. A couple
# of other common variants are tried too so a different district's date
# format degrades to "no due date" (still shown, just unsorted) instead of
# crashing the whole list.
_DUE_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y")


def _parse_due_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in _DUE_DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _item_uid(student_id: str, assignment: MissingAssignment) -> str:
    # Synthesized, not a real PowerSchool ID -- the missing-assignments page
    # doesn't expose one. Stable across polls as long as the assignment's
    # own fields don't change, which is enough to keep HA from treating an
    # unchanged item as a new one on every refresh.
    key = "|".join(
        [
            student_id,
            assignment.course_name,
            assignment.assignment_name or "",
            assignment.due_date or "",
            assignment.category or "",
        ]
    )
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PowerSchoolCoordinator = hass.data[DOMAIN][entry.entry_id][DATA_COORDINATOR]

    entities = [
        PowerSchoolMissingAssignmentsTodoList(coordinator, student_id) for student_id in coordinator.data
    ]
    async_add_entities(entities)


class PowerSchoolMissingAssignmentsTodoList(PowerSchoolStudentEntity, TodoListEntity):
    """Read-only to-do list of one student's currently-missing assignments."""

    _attr_icon = "mdi:clipboard-alert-outline"

    def __init__(self, coordinator: PowerSchoolCoordinator, student_id: str) -> None:
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{DOMAIN}_{student_id}_missing_assignments_todo"

    @property
    def name(self) -> str:
        data = self._student_data
        return f"{data.student.name} Missing Assignments" if data else "PowerSchool Missing Assignments"

    @property
    def todo_items(self) -> list[TodoItem]:
        data = self._student_data
        if not data:
            return []

        items = []
        for assignment in data.missing_assignments:
            description_parts = [assignment.category]
            if assignment.teacher_name:
                description_parts.append(f"Teacher: {assignment.teacher_name}")
            items.append(
                TodoItem(
                    uid=_item_uid(self._student_id, assignment),
                    summary=f"{assignment.course_name} — {assignment.assignment_name or 'Assignment'}",
                    status=TodoItemStatus.NEEDS_ACTION,
                    due=_parse_due_date(assignment.due_date),
                    description=" / ".join(p for p in description_parts if p) or None,
                )
            )
        return items
