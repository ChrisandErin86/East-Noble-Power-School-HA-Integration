"""DataUpdateCoordinator for the PowerSchool Grades & Attendance integration."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HistoricalYear,
    PowerSchoolAuthError,
    PowerSchoolClient,
    PowerSchoolError,
    PowerSchoolStudent,
    PowerSchoolStudentData,
)
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    GRADE_HISTORY_SCAN_INTERVAL_HOURS,
)

_LOGGER = logging.getLogger(__name__)


class PowerSchoolCoordinator(DataUpdateCoordinator[dict[str, PowerSchoolStudentData]]):
    """Logs in once per refresh cycle and pulls every linked student's data.

    One login, then one student-switch + page fetch per student -- the
    guardian portal only tracks one "active student" per session, so this
    can't be parallelized across students within a single client/session.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        interval_minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=interval_minutes),
        )
        self.entry = entry
        # A dedicated cookie jar so this integration's PowerSchool session
        # cookies never mix with HA's shared aiohttp session used by other
        # integrations.
        self._session = aiohttp_client.async_create_clientsession(hass)
        self.client = PowerSchoolClient(
            session=self._session,
            host=entry.data[CONF_HOST],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        )

    async def _async_update_data(self) -> dict[str, PowerSchoolStudentData]:
        try:
            students = await self.client.async_get_students()
            if not students:
                # Single-student accounts may not expose switchStudent()
                # links at all -- fall back to whatever's already active.
                students = [PowerSchoolStudent(student_id="", name=self.entry.data[CONF_USERNAME])]

            result: dict[str, PowerSchoolStudentData] = {}
            for student in students:
                key = student.student_id or "default"
                result[key] = await self.client.async_get_student_data(student)
            return result
        except PowerSchoolAuthError as err:
            raise UpdateFailed(f"PowerSchool authentication failed: {err}") from err
        except PowerSchoolError as err:
            raise UpdateFailed(f"Error reading PowerSchool: {err}") from err

    async def async_shutdown(self) -> None:
        await self._session.close()
        await super().async_shutdown()


class PowerSchoolHistoryCoordinator(DataUpdateCoordinator[dict[str, list[HistoricalYear]]]):
    """Refreshes completed-year Grade History on its own, much slower schedule.

    A closed-out school year's grades don't change, so there's no reason to
    re-fetch several years of history on the same 15-45 minute cadence as
    the live term data -- see GRADE_HISTORY_SCAN_INTERVAL_HOURS in const.py.
    This runs its own PowerSchoolClient / aiohttp session rather than
    sharing PowerSchoolCoordinator's: the guardian portal only tracks one
    "active student" per session, so two coordinators refreshing through
    the same client could stomp on each other's student-switch if their
    schedules ever overlapped.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_history",
            update_interval=timedelta(hours=GRADE_HISTORY_SCAN_INTERVAL_HOURS),
        )
        self.entry = entry
        self._session = aiohttp_client.async_create_clientsession(hass)
        self.client = PowerSchoolClient(
            session=self._session,
            host=entry.data[CONF_HOST],
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
        )

    async def _async_update_data(self) -> dict[str, list[HistoricalYear]]:
        try:
            students = await self.client.async_get_students()
            if not students:
                students = [PowerSchoolStudent(student_id="", name=self.entry.data[CONF_USERNAME])]

            result: dict[str, list[HistoricalYear]] = {}
            for student in students:
                key = student.student_id or "default"
                result[key] = await self.client.async_fetch_grade_history(student)
            return result
        except PowerSchoolAuthError as err:
            raise UpdateFailed(f"PowerSchool authentication failed: {err}") from err
        except PowerSchoolError as err:
            raise UpdateFailed(f"Error reading PowerSchool grade history: {err}") from err

    async def async_shutdown(self) -> None:
        await self._session.close()
        await super().async_shutdown()
