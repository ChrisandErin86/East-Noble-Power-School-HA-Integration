"""Constants for the PowerSchool Grades & Attendance integration."""

DOMAIN = "powerschool_grades"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"

# Minutes. PowerSchool's guardian portal publishes no documented rate limit,
# so this stays conservative by default -- there's no reason to hammer it,
# grades/attendance don't change minute to minute. Adjust via the options
# flow if you want it tighter.
DEFAULT_SCAN_INTERVAL_MINUTES = 45
MIN_SCAN_INTERVAL_MINUTES = 15

# Keys into hass.data[DOMAIN][entry.entry_id] -- one config entry now holds
# two coordinators (see coordinator.py).
DATA_COORDINATOR = "coordinator"
DATA_HISTORY_COORDINATOR = "history_coordinator"

# Hours. Grade History only ever covers school years PowerSchool has already
# closed out, so there's nothing to gain from polling it anywhere near as
# often as the live, in-progress data -- it gets its own, much longer
# interval and its own login session (see PowerSchoolHistoryCoordinator).
GRADE_HISTORY_SCAN_INTERVAL_HOURS = 24
