"""Sensor platform for PowerSchool Grades & Attendance."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PowerSchoolCourseRow
from .const import DOMAIN
from .coordinator import PowerSchoolCoordinator
from .entity import PowerSchoolStudentEntity as _PowerSchoolStudentEntity


def _to_int(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PowerSchoolCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for student_id, student_data in coordinator.data.items():
        entities.append(PowerSchoolGpaSensor(coordinator, student_id))
        entities.append(PowerSchoolAttendanceSummarySensor(coordinator, student_id))
        entities.append(PowerSchoolMissingAssignmentsSensor(coordinator, student_id))
        for course_index in range(len(student_data.courses)):
            entities.append(PowerSchoolCourseGradeSensor(coordinator, student_id, course_index))

    async_add_entities(entities)


class PowerSchoolGpaSensor(_PowerSchoolStudentEntity, SensorEntity):
    """Current-term GPA, when the portal publishes one for this student."""

    _attr_icon = "mdi:school"

    def __init__(self, coordinator: PowerSchoolCoordinator, student_id: str) -> None:
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{DOMAIN}_{student_id}_gpa"

    @property
    def name(self) -> str:
        data = self._student_data
        return f"{data.student.name} GPA" if data else "PowerSchool GPA"

    @property
    def native_value(self) -> str | None:
        data = self._student_data
        return data.gpa if data else None


class PowerSchoolAttendanceSummarySensor(_PowerSchoolStudentEntity, SensorEntity):
    """Total absences across every current class, with tardies and a per-course breakdown.

    This rolls up the same Absences/Tardies columns already on each course
    row -- it doesn't scrape the separate day-by-day Attendance History page
    (that page is a much bigger per-day/per-period grid spanning the whole
    year; a good v2 if you want attendance CODES, not just totals).
    """

    _attr_icon = "mdi:calendar-check"
    _attr_native_unit_of_measurement = "absences"

    def __init__(self, coordinator: PowerSchoolCoordinator, student_id: str) -> None:
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{DOMAIN}_{student_id}_attendance"

    @property
    def name(self) -> str:
        data = self._student_data
        return f"{data.student.name} Attendance" if data else "PowerSchool Attendance"

    @property
    def native_value(self) -> int | None:
        data = self._student_data
        if not data:
            return None
        return sum(_to_int(c.absences) for c in data.courses)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._student_data
        if not data:
            return {}
        return {
            "total_tardies": sum(_to_int(c.tardies) for c in data.courses),
            "per_course": {
                c.course_name: {"absences": _to_int(c.absences), "tardies": _to_int(c.tardies)}
                for c in data.courses
            },
        }


class PowerSchoolMissingAssignmentsSensor(_PowerSchoolStudentEntity, SensorEntity):
    """Count of currently-missing assignments, with the full list as an attribute."""

    _attr_icon = "mdi:clipboard-alert-outline"
    _attr_native_unit_of_measurement = "assignments"

    def __init__(self, coordinator: PowerSchoolCoordinator, student_id: str) -> None:
        super().__init__(coordinator, student_id)
        self._attr_unique_id = f"{DOMAIN}_{student_id}_missing_assignments"

    @property
    def name(self) -> str:
        data = self._student_data
        return f"{data.student.name} Missing Assignments" if data else "PowerSchool Missing Assignments"

    @property
    def native_value(self) -> int | None:
        data = self._student_data
        return len(data.missing_assignments) if data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self._student_data
        if not data:
            return {}
        return {
            "assignments": [
                {
                    "course": a.course_name,
                    "due_date": a.due_date,
                    "assignment": a.assignment_name,
                    "category": a.category,
                    "teacher": a.teacher_name,
                }
                for a in data.missing_assignments
            ]
        }


class PowerSchoolCourseGradeSensor(_PowerSchoolStudentEntity, SensorEntity):
    """One sensor per course row on the grades/attendance table.

    State is the most recently populated grading period's grade (works for
    both 2-semester and 4-marking-period grading schemes -- the number of
    periods isn't fixed, see api.py). All periods are exposed as attributes
    regardless of which one is "current".

    Caveat: course_index is positional (the Nth row in the table), so if a
    student's schedule changes mid-term the row order can shift and this
    entity can end up pointing at a different class after a refresh. Fine
    for dashboards/notifications; don't rely on entity_id staying pinned to
    one specific course across a schedule change.
    """

    _attr_icon = "mdi:notebook-outline"

    def __init__(self, coordinator: PowerSchoolCoordinator, student_id: str, course_index: int) -> None:
        super().__init__(coordinator, student_id)
        self._course_index = course_index
        self._attr_unique_id = f"{DOMAIN}_{student_id}_course_{course_index}"

    @property
    def _course(self) -> PowerSchoolCourseRow | None:
        data = self._student_data
        if not data or self._course_index >= len(data.courses):
            return None
        return data.courses[self._course_index]

    @property
    def name(self) -> str:
        data = self._student_data
        course = self._course
        if not data or not course:
            return "PowerSchool Course"
        return f"{data.student.name} - {course.course_name}"

    @property
    def native_value(self) -> str | None:
        course = self._course
        return course.current_grade if course else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        course = self._course
        if not course:
            return {}
        return {
            "teacher": course.teacher_name,
            "grades": course.grades,
            "absences": course.absences,
            "tardies": course.tardies,
        }
