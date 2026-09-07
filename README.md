# PowerSchool Grades & Attendance for Home Assistant

A custom Home Assistant integration that pulls per-course grades, attendance,
and GPA out of a district's PowerSchool **guardian portal** and exposes them
as sensors. There is no public API for parent/guardian accounts, so this
works by logging in and parsing the same HTML pages your browser loads --
it is a scraper, not a client for an official PowerSchool API.

Built and verified against a live district instance (East Noble, IN) on
2026-09-06/09-07, against two students on two different grading schemes (a
high schooler on semesters, an elementary student on marking periods).
Other districts run the same PowerSchool SIS software but can be on
different versions or have different template customizations -- see
"When it breaks" below.

## What it gives you

- One Home Assistant "device" per linked student (multi-student guardian
  accounts are handled automatically via the portal's student switcher).
- A GPA sensor per student, when the portal publishes one.
- A sensor per course row on the grades/attendance table. State is the most
  recently populated grading period's grade -- works whether the district
  grades on 2 semesters or 4 marking periods, since the number of periods
  isn't hardcoded. Attributes include teacher name, every grading period's
  grade (not just the current one), absences, and tardies.
- An Attendance sensor per student: state is total absences across every
  current class, with total tardies and a per-course breakdown as
  attributes. (This rolls up the same per-course totals already on the
  grades table -- it does not scrape the separate day-by-day Attendance
  History page, which is a much bigger year-long per-period grid of
  attendance codes. Good v2 if you want that level of detail.)
- A Missing Assignments sensor per student: state is the count of currently
  missing assignments, with the full list (course, due date, assignment
  name, category, teacher) as an attribute.
- A Missing Assignments **to-do list** per student (needs Home Assistant
  2024.2+), mirroring the same data as an actual to-do list entity instead
  of a sensor attribute -- drop a native "To-do List" card on it and get a
  readable, sortable-by-due-date list with no templating required. It's
  read-only on purpose: this list is recomputed from PowerSchool on every
  poll, so there's nothing sensible for "check off" or "delete" to do --
  neither is offered, and the list just reflects whatever the portal says
  is currently missing.

## Installation

1. Copy `custom_components/powerschool_grades/` into your Home Assistant
   config directory, so you end up with
   `<config>/custom_components/powerschool_grades/`.
2. Restart Home Assistant.
3. Settings -> Devices & Services -> Add Integration -> "PowerSchool Grades
   & Attendance".
4. Enter the portal host **without** `https://` (e.g.
   `powerschool.eastnoble.net`), and your guardian username/password. The
   config flow logs in for real before saving, so a typo or bad password
   fails immediately instead of silently.

Poll interval defaults to 45 minutes; change it from the integration's
"Configure" options after setup. There's no published rate limit for the
guardian portal, so this stays conservative on purpose -- grades and
attendance don't need minute-level freshness, and hammering a school's login
endpoint is a good way to get an account flagged or locked.

## How the login actually works

`custom_components/powerschool_grades/api.py` has the details, but in short:

- `GET /public/` to get a session cookie and the login form's hidden fields.
- `POST` the form back to `/guardian/home.html` with `account`/`pw` set to
  your credentials, and `dbpw`/`ldappassword` mirroring `pw` in plaintext
  (over HTTPS) -- that's what this district's own
  `/admin/javascript/signin-script.js` does client-side (its
  `encryptGuardianPassword()` etc. are no-op stubs in this build).
- Older PowerSchool deployments may still RC4-encrypt the password using
  `md5(contextData)` as the key before submitting it, instead of sending it
  plain. If login silently fails against a different district, fetch that
  district's `signin-script.js` unauthenticated and check whether
  `encryptGuardianPassword` is still a real function -- that tells you which
  scheme to implement.
- Multi-student accounts switch the "active student" in the session with a
  `POST /guardian/home.html` carrying `selected_student_id=<id>`; student
  IDs and names come from `switchStudent(<id>)` links in the page.
- The grades/attendance table is found by its `<caption>` text
  ("Attendance By Class"). Column count is *not* fixed -- the number of
  grading-period columns depends on the student's grading scheme (2 for
  semesters, 4 for marking periods, seen so far). The row layout is some
  number of attendance-grid cells (nominally 11, one per school day in the
  last/this-week columns), then course+teacher, then N grading-period
  cells, then absences, then tardies -- but the attendance-grid cell count
  isn't fixed either: a class that doesn't meet every day can have some of
  those day cells collapsed with `colspan`, which shifts every following
  index left. So the course cell is located by *content* (it's the first
  `<td>` containing an "Email teacher" link or a "Details about teacher"
  title), not by a fixed position -- everything else (grades, absences,
  tardies) is then read relative to that cell, and the header row's
  Course/Absences column labels, not a raw cell-count guess. If a
  district's template differs, this is the first thing to adjust.
- Missing assignments come from `/guardian/missingasmts.html`, matched by
  its header row (`Course | Due Date | Assignment | Category | Teacher`)
  rather than a CSS class, since PowerSchool reuses generic `table.grid`
  classing all over the portal.
- The client logs in once and reuses the session across polls (an aiohttp
  cookie jar), rather than re-logging in every 15-45+ minute refresh. If
  the portal's own session cookie expires faster than that -- observed in
  practice: after a while, every authenticated page silently comes back as
  the sign-in page again, no error, no redirect -- every fetch checks for
  that (`_looks_signed_out()` in `api.py`) and transparently re-logs in and
  retries once before giving up. Without this, a dead session doesn't fail
  loudly: it just looks like "no students found," which then looks like
  "no classes found," which shows up in HA as every sensor going stuck on
  `unknown` -- confusing, since it's not an `UpdateFailed`/unavailable
  state, just stale data forever until the integration reloads.

## When it breaks

This is HTML scraping against a UI PowerSchool and individual districts can
change without notice -- a PowerSchool SIS version bump, a district
switching to the newer Angular-based portal UI, or even a template tweak
can all break parsing silently (you'll see `UpdateFailed` / stale sensors,
not a clean error). Treat this as "needs occasional maintenance," not
"install and forget."

## Legal/ToS note

You're scraping your own guardian account's data, which is materially
different from scraping someone else's -- but PowerSchool's and/or your
district's terms of service may still have language against automated
access. Worth a skim before you rely on this, and worth keeping the poll
interval modest so it doesn't look like abuse.

## Ideas for v2 (not implemented here)

- Day-by-day attendance codes from the Attendance History page (present vs.
  a specific code like unexcused/tardy-excused/etc, per period, per day) --
  currently only totals are exposed, via the Attendance sensor above.
- Per-assignment detail via the `scores.html?frn=...&fg=S1` links already
  present on each grade cell, for a "new grade posted" binary_sensor to
  drive a notification automation.
- Config-flow validation of the *host* itself (right now a bad hostname just
  surfaces as `cannot_connect`, which is accurate but not very specific).
