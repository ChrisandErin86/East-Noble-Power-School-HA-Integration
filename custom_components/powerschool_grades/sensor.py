"""Sensor platform for PowerSchool Grades & Attendance."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PowerSchoolCourseRow, PowerSchoolStudentData
from .const import DOMAIN
from .coordinator import PowerSchoolCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: PowerSchoolCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = []
    for student_id, student_data in coordinator.data.items():
        entities.append(PowerSchoolGpaSensor(coordinator, student_id))
        for course_index in range(len(student_data.courses)):
            entities.append(PowerSchoolCourseGradeSensor(coordinator, student_id, course_index))

    async_add_entities(entities)


class _PowerSchoolStudentEntity(CoordinatorEntity[PowerSchoolCoordinator]):
    """Shared lookup of this entity's current student record.

    Every property re-reads from coordinator.data on each access rather than
    caching, since the coordinator swaps the whole dict on every refresh.
    """

    def __init__(self, coordinator: PowerSchoolCoordinator, student_id: str) -> None:
        super().__init__(coordinator)
        self._student_id = student_id

    @property
    def _student_data(self) -> PowerSchoolStudentData | None:
        return self.coordinator.data.get(self._student_id)

    @property
    def device_info(self) -> DeviceInfo:
        data = self._student_data
        name = data.student.name if data else self._student_id
        return DeviceInfo(
            identifiers={(DOMAIN, self._student_id)},
            name=f"PowerSchool - {name}",
            manufacturer="PowerSchool (unofficial integration)",
        )


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


class PowerSchoolCourseGradeSensor(_PowerSchoolStudentEntity, SensorEntity):
    """One sensor per course row on the grades/attendance table.

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
        return course.term1_grade if course else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        course = self._course
        if not course:
            return {}
        return {
            "teacher": course.teacher_name,
            "term1_grade": course.term1_grade,
            "term2_grade": course.term2_grade,
            "absences": course.absences,
            "tardies": course.tardies,
        }
