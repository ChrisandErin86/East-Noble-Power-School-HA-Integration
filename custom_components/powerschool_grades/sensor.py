"""Sensor platform for PowerSchool Grades & Attendance."""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import PowerSchoolCourseRow
from .const import DATA_COORDINATOR, DATA_HISTORY_COORDINATOR, DOMAIN
from .coordinator import PowerSchoolCoordinator, PowerSchoolHistoryCoordinator
from .entity import PowerSchoolStudentEntity as _PowerSchoolStudentEntity


def _to_int(value: str | None) -> int:
    try:
        return int(value) if value is not None else 0
    except ValueError:
        return 0


def _to_int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


_YEAR_LABEL_RE = re.compile(r"^(\d{2})-(\d{2})$")


def _approx_term_date(year_label: str, term_index: int, term_count: int) -> str | None:
    """A rough calendar date to plot a historical term at -- not a real end date.

    Grade History gives a school-year range and a term label (T1, H1,
    M1..M4, ...) but no actual dates. For charting a multi-year trend,
    spreading a year's terms evenly across an assumed Aug 15 - Jun 15
    school year is enough to get chronological order and rough season
    right; it doesn't claim to be when that term actually ended.
    """
    match = _YEAR_LABEL_RE.match(year_label)
    if not match:
        return None
    start = date(2000 + int(match.group(1)), 8, 15)
    end = date(2000 + int(match.group(2)), 6, 15)
    offset_days = int((end - start).days * (term_index + 1) / (term_count + 1))
    return (start + timedelta(days=offset_days)).isoformat()


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PowerSchoolCoordinator = entry_data[DATA_COORDINATOR]
    history_coordinator: PowerSchoolHistoryCoordinator = entry_data[DATA_HISTORY_COORDINATOR]

    entities: list[SensorEntity] = []
    for student_id, student_data in coordinator.data.items():
        entities.append(PowerSchoolGpaSensor(coordinator, student_id))
        entities.append(PowerSchoolAttendanceSummarySensor(coordinator, student_id))
        entities.append(PowerSchoolMissingAssignmentsSensor(coordinator, student_id))
        entities.append(PowerSchoolGradeHistorySensor(coordinator, history_coordinator, student_id))
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


class PowerSchoolGradeHistorySensor(_PowerSchoolStudentEntity, SensorEntity):
    """Completed school years' final grades, from the separate Grade History page.

    Unlike the course sensors above (which read the in-progress current
    year off the home page), PowerSchool only lists a year here once it's
    finished -- so this has nothing to do with "today's grade in Algebra."
    It refreshes on its own, much slower schedule (PowerSchoolHistoryCoordinator
    in coordinator.py) since a closed-out year's grades don't change poll to
    poll. State is just the count of completed years on file; the data is
    in two attributes:

    - `years`: nested year -> term -> course, meant for a Markdown card
      table.
    - `course_percent_history`: the same rows flattened to one per
      course/term, in chronological order, each with an estimated `date`
      (see _approx_term_date) -- meant to feed an apexcharts-card
      data_generator for a multi-year trend line, e.g. filtering this list
      to one course and mapping it to `[row.date, row.percent]` pairs.
    """

    _attr_icon = "mdi:chart-line"

    def __init__(
        self,
        coordinator: PowerSchoolCoordinator,
        history_coordinator: PowerSchoolHistoryCoordinator,
        student_id: str,
    ) -> None:
        super().__init__(coordinator, student_id)
        self._history_coordinator = history_coordinator
        self._attr_unique_id = f"{DOMAIN}_{student_id}_grade_history"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # CoordinatorEntity already wires up listening to the main
        # coordinator; this entity also needs to react to the *history*
        # coordinator's own refreshes, which land on a separate schedule.
        self.async_on_remove(
            self._history_coordinator.async_add_listener(self._handle_coordinator_update)
        )

    @property
    def _years(self) -> list:
        data = self._history_coordinator.data
        return data.get(self._student_id, []) if data else []

    @property
    def name(self) -> str:
        data = self._student_data
        return f"{data.student.name} Grade History" if data else "PowerSchool Grade History"

    @property
    def native_value(self) -> int:
        return len(self._years)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        years_payload = []
        course_percent_history = []

        for year in self._years:
            terms_payload = [
                {
                    "term": term.term_label,
                    "courses": [
                        {
                            "course": c.course_name,
                            "grade": c.grade,
                            "percent": _to_int_or_none(c.percent),
                            "citizenship": c.citizenship,
                            "hours": c.hours,
                        }
                        for c in term.courses
                    ],
                }
                for term in year.terms
            ]
            years_payload.append(
                {"year": year.year_label, "school": year.school_label, "terms": terms_payload}
            )

            term_count = len(year.terms)
            for term_index, term in enumerate(year.terms):
                approx_date = _approx_term_date(year.year_label, term_index, term_count)
                for c in term.courses:
                    course_percent_history.append(
                        {
                            "year": year.year_label,
                            "term": term.term_label,
                            "course": c.course_name,
                            "grade": c.grade,
                            "percent": _to_int_or_none(c.percent),
                            "date": approx_date,
                        }
                    )

        return {
            "years": years_payload,
            "course_percent_history": course_percent_history,
        }
