"""Shared entity base class for every PowerSchool Grades & Attendance platform.

Split out of sensor.py once the todo platform needed the same student
lookup + device grouping -- both platforms build one "device" per linked
student and read that student's latest scrape off the shared coordinator.
"""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PowerSchoolStudentData
from .const import DOMAIN
from .coordinator import PowerSchoolCoordinator


class PowerSchoolStudentEntity(CoordinatorEntity[PowerSchoolCoordinator]):
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
            manufacturer="PowerSchool",
        )
