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
