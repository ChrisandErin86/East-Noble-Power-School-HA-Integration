"""The PowerSchool Grades & Attendance integration.

Screen-scrapes a district's PowerSchool guardian portal -- there is no
public API for parent accounts. See custom_components/powerschool_grades/api.py
for how the login and parsing work, and README.md for setup and caveats.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DATA_COORDINATOR, DATA_HISTORY_COORDINATOR, DOMAIN
from .coordinator import PowerSchoolCoordinator, PowerSchoolHistoryCoordinator

PLATFORMS = ["sensor", "todo"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = PowerSchoolCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    history_coordinator = PowerSchoolHistoryCoordinator(hass, entry)
    # Best-effort, not a first_refresh(): Grade History is a slow secondary
    # feature (see coordinator.py), so it failing or just not having run
    # yet shouldn't hold up the rest of the integration coming up. Its
    # entity reads coordinator.data defensively and fills in once this
    # succeeds on its own schedule.
    await history_coordinator.async_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        DATA_HISTORY_COORDINATOR: history_coordinator,
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        await entry_data[DATA_COORDINATOR].async_shutdown()
        await entry_data[DATA_HISTORY_COORDINATOR].async_shutdown()
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
