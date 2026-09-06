# PowerSchool Grades & Attendance (Unofficial) for Home Assistant

A custom Home Assistant integration that pulls per-course grades, attendance,
and GPA out of a district's PowerSchool **guardian portal** and exposes them
as sensors. There is no public API for parent/guardian accounts, so this
works by logging in and parsing the same HTML pages your browser loads --
it is a scraper, not a client for an official PowerSchool API.

Built and verified against a live district instance (East Noble, IN) on
2026-09-06. Other districts run the same PowerSchool SIS software but can be
on different versions or have different template customizations -- see
"When it breaks" below.

## What it gives you

- One Home Assistant "device" per linked student (multi-student guardian
  accounts are handled automatically via the portal's student switcher).
- A GPA sensor per student, when the portal publishes one.
- A sensor per course row on the grades/attendance table, with state =
  current term grade and attributes for teacher name, term 1/term 2 grade,
  absences, and tardies.

## Installation

1. Copy `custom_components/powerschool_grades/` into your Home Assistant
   config directory, so you end up with
   `<config>/custom_components/powerschool_grades/`.
2. Restart Home Assistant.
3. Settings -> Devices & Services -> Add Integration -> "PowerSchool Grades
   & Attendance (Unofficial)".
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
  ("Attendance By Class") and parsed as a fixed 16-cell-per-row table. If a
  district's template differs, this is the first thing to adjust.

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

- A "Missing Assignments" sensor -- the portal has a separate nav page for
  this; same scraping approach, different URL/table.
- Per-assignment detail via the `scores.html?frn=...&fg=S1` links already
  present on each grade cell, for a "new grade posted" binary_sensor to
  drive a notification automation.
- Config-flow validation of the *host* itself (right now a bad hostname just
  surfaces as `cannot_connect`, which is accurate but not very specific).
