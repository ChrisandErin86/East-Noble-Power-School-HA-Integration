"""Unofficial PowerSchool guardian ("PCAS") portal client.

There is no public JSON API for parent/guardian accounts -- this scrapes the
same server-rendered HTML pages a browser would load. It was reverse
engineered against a live PowerSchool SIS instance on 2026-09-06/09-07
(assets tagged mba-core 26.1) and confirmed against that district's real
sign-in form, grades table, missing-assignments page, and against two
different students on two different grading schemes (a high schooler on
semesters S1/S2, an elementary student on marking periods M1-M4). Two
things are district/version-specific and are the first places to look if
this breaks against a different school:

1. Password submission. This district's /admin/javascript/signin-script.js
   ships encryptGuardianPassword() and friends as no-op stubs -- doPCASLogin()
   just copies the plaintext password into the `dbpw` and `ldappassword`
   fields and relies on TLS. Older PowerSchool builds instead RC4-encrypt the
   password using md5(contextData) as the key, where contextData is a hidden
   field on the login page. If login silently fails (still on the sign-in
   page after POST), fetch https://<host>/admin/javascript/signin-script.js
   unauthenticated and grep for "encryptGuardianPassword" to see which
   scheme that district runs, and add the RC4 path here if needed.

2. Grades table shape. The grades+attendance table is identified by its
   <caption> text "Attendance By Class". Column count is NOT fixed -- the
   number of grading-period columns depends on the student's grading scheme
   (2 for semester-based, 4 for marking-period-based elementary schools,
   possibly others). The layout is: [0] expected-period, [1-5] last week
   Mon-Fri, [6-10] this week Mon-Fri, [11] course name + teacher link,
   [12 .. -3] one cell per grading period (labels come from the header row,
   e.g. S1/S2 or M1-M4), [-2] absences, [-1] tardies. Course name is the
   cell's first direct text node (BeautifulSoup .stripped_strings), NOT
   cell.get_text(), because the cell also embeds an "Email <teacher>" link
   and a "- Rm: <room>" span that get_text() would mash into the name.
   If parsing comes back empty, log the raw table HTML and adjust
   _parse_student_data().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import aiohttp
from bs4 import BeautifulSoup

_LOGGER = logging.getLogger(__name__)

LOGIN_PAGE_PATH = "/public/"
GUARDIAN_HOME_PATH = "/guardian/home.html"
MISSING_ASSIGNMENTS_PATH = "/guardian/missingasmts.html"
STUDENT_LINK_RE = re.compile(r"switchStudent\((\d+)\)")
GPA_RE = re.compile(r"GPA\s*\(S\d\):\s*([\d.]+)")
GRADES_TABLE_CAPTION = "Attendance By Class"
# Grade cells that just link to a not-yet-graded period render as a lone
# "info" glyph (literal text like "[ i ]" or "i"), not an empty cell.
NOT_YET_GRADED_RE = re.compile(r"^\[?\s*i\s*\]?$", re.IGNORECASE)


class PowerSchoolAuthError(Exception):
    """Login failed: bad credentials, locked account, or MFA/SSO required."""


class PowerSchoolError(Exception):
    """Something about the portal didn't match what this client expects."""


@dataclass
class PowerSchoolStudent:
    student_id: str
    name: str


@dataclass
class PowerSchoolCourseRow:
    course_name: str
    teacher_name: str | None
    # Keyed by whatever the portal labels the grading period as (e.g. "S1",
    # "S2", or "M1".."M4") -- HA can't assume there are exactly two terms.
    grades: dict[str, str | None]
    absences: str | None
    tardies: str | None

    @property
    def current_grade(self) -> str | None:
        """The most recently populated grading period, or None if none are graded yet."""
        current = None
        for value in self.grades.values():
            if value is not None:
                current = value
        return current


@dataclass
class MissingAssignment:
    course_name: str
    due_date: str | None
    assignment_name: str | None
    category: str | None
    teacher_name: str | None


@dataclass
class PowerSchoolStudentData:
    student: PowerSchoolStudent
    courses: list[PowerSchoolCourseRow] = field(default_factory=list)
    gpa: str | None = None
    missing_assignments: list[MissingAssignment] = field(default_factory=list)


class PowerSchoolClient:
    """One logged-in session against one district's guardian portal.

    Not thread-safe. Call sites (the coordinator) are expected to serialize
    access -- there's only one "active student" per session, so switching
    students and reading their data has to happen without another call
    interleaving.
    """

    def __init__(self, session: aiohttp.ClientSession, host: str, username: str, password: str) -> None:
        self._session = session
        self._base_url = f"https://{host.strip().rstrip('/')}"
        self._username = username
        self._password = password
        self._logged_in = False

    async def async_login(self) -> None:
        """Authenticate, replacing any prior session state."""
        async with self._session.get(f"{self._base_url}{LOGIN_PAGE_PATH}") as resp:
            resp.raise_for_status()
            login_html = await resp.text()

        soup = BeautifulSoup(login_html, "html.parser")
        form = soup.find("form", id="LoginForm") or soup.find("form", attrs={"name": "LoginForm"})
        if form is None:
            raise PowerSchoolError(
                "Sign-in form not found at /public/ -- portal markup has changed, or this "
                "isn't a PowerSchool guardian portal."
            )

        action = form.get("action") or GUARDIAN_HOME_PATH
        payload: dict[str, str] = {}
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")

        payload["account"] = self._username
        payload["pw"] = self._password
        # See module docstring: this mirrors doPCASLogin() in the district's
        # own signin-script.js. Swap in RC4(md5(contextData), password) here
        # if a target district still uses the legacy scheme.
        payload["dbpw"] = self._password
        payload["ldappassword"] = self._password

        post_url = action if action.startswith("http") else f"{self._base_url}/{action.lstrip('/')}"
        async with self._session.post(post_url, data=payload) as resp:
            resp.raise_for_status()
            result_html = await resp.text()

        if 'id="LoginForm"' in result_html or 'id="pslogin"' in result_html:
            raise PowerSchoolAuthError(
                "Still on the sign-in page after submitting credentials -- wrong "
                "username/password, a locked account, or the district requires "
                "MFA/SSO that this client can't do."
            )
        self._logged_in = True

    async def _ensure_login(self) -> None:
        if not self._logged_in:
            await self.async_login()

    async def async_get_students(self) -> list[PowerSchoolStudent]:
        """List every student linked to this guardian account."""
        await self._ensure_login()
        async with self._session.get(f"{self._base_url}{GUARDIAN_HOME_PATH}") as resp:
            resp.raise_for_status()
            html = await resp.text()
        return self._parse_students(html)

    @staticmethod
    def _parse_students(html: str) -> list[PowerSchoolStudent]:
        soup = BeautifulSoup(html, "html.parser")
        students: list[PowerSchoolStudent] = []
        seen: set[str] = set()
        for anchor in soup.find_all("a", href=True):
            match = STUDENT_LINK_RE.search(anchor["href"])
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                students.append(PowerSchoolStudent(student_id=match.group(1), name=anchor.get_text(strip=True)))
        if not students:
            # Single-student accounts sometimes skip the tab bar entirely.
            _LOGGER.debug("No switchStudent() links found; assuming a single-student account.")
        return students

    async def async_switch_student(self, student_id: str) -> None:
        await self._ensure_login()
        async with self._session.post(
            f"{self._base_url}{GUARDIAN_HOME_PATH}", data={"selected_student_id": student_id}
        ) as resp:
            resp.raise_for_status()

    async def async_get_student_data(self, student: PowerSchoolStudent) -> PowerSchoolStudentData:
        """Switch to this student's context and scrape their grades + missing-assignments pages."""
        if student.student_id:
            await self.async_switch_student(student.student_id)
        else:
            await self._ensure_login()

        async with self._session.get(f"{self._base_url}{GUARDIAN_HOME_PATH}") as resp:
            resp.raise_for_status()
            grades_html = await resp.text()
        data = self._parse_student_data(student, grades_html)

        # Same active-student session context, no need to switch again.
        async with self._session.get(f"{self._base_url}{MISSING_ASSIGNMENTS_PATH}") as resp:
            resp.raise_for_status()
            missing_html = await resp.text()
        data.missing_assignments = self._parse_missing_assignments(missing_html)

        return data

    @staticmethod
    def _parse_student_data(student: PowerSchoolStudent, html: str) -> PowerSchoolStudentData:
        soup = BeautifulSoup(html, "html.parser")
        data = PowerSchoolStudentData(student=student)

        table = None
        for candidate in soup.find_all("table"):
            caption = candidate.find("caption")
            if caption and caption.get_text(strip=True) == GRADES_TABLE_CAPTION:
                table = candidate
                break

        if table is None:
            _LOGGER.warning(
                "No '%s' table found for %s -- portal layout may have changed, "
                "or this student has no current classes.",
                GRADES_TABLE_CAPTION,
                student.name,
            )
            return data

        # Grading-period column labels (e.g. ["S1", "S2"] or ["M1".."M4"])
        # come from the header row: everything between the "Course" header
        # and the "Absences" header.
        period_labels: list[str] = []
        for row in table.find_all("tr"):
            headers = row.find_all("th")
            if not headers:
                continue
            texts = [h.get_text(strip=True) for h in headers]
            if "Course" in texts and "Absences" in texts:
                start = texts.index("Course") + 1
                end = texts.index("Absences")
                period_labels = texts[start:end]
                break

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue  # header/spacer rows aren't course rows

            # The course cell is identified by content (an "Email <teacher>"
            # link, or a "Details about <teacher>" title), NOT by a fixed
            # index. The attendance-grid cells before it (nominally 11, one
            # per school day in the last/this week columns) can be fewer
            # than that on a row whose class doesn't meet every day -- some
            # districts collapse non-meeting days with colspan, which shifts
            # every following index left. Locating the course cell by what's
            # actually in it keeps this working regardless of how many
            # attendance cells came before it.
            course_idx = None
            teacher_name = None
            for idx, cell in enumerate(cells):
                found_teacher = None
                for link in cell.find_all("a"):
                    title = link.get("title", "")
                    if title.startswith("Details about "):
                        found_teacher = title.replace("Details about ", "").strip() or None
                        break
                    link_text = link.get_text(strip=True)
                    if link_text.lower().startswith("email "):
                        found_teacher = link_text[len("email "):].strip() or None
                        break
                if found_teacher is not None:
                    course_idx = idx
                    teacher_name = found_teacher
                    break

            if course_idx is None or len(cells) - course_idx < 3:
                continue  # not a course row (header/spacer), or too short to hold grades+absences+tardies

            course_cell = cells[course_idx]
            absences_cell = cells[-2]
            tardies_cell = cells[-1]
            if period_labels:
                grade_cells = cells[course_idx + 1 : course_idx + 1 + len(period_labels)]
            else:
                grade_cells = cells[course_idx + 1 : -2]

            course_name = next(course_cell.stripped_strings, "").strip()
            if not course_name:
                continue

            grades: dict[str, str | None] = {}
            for idx, cell in enumerate(grade_cells):
                label = period_labels[idx] if idx < len(period_labels) else f"period_{idx + 1}"
                text = cell.get_text(" ", strip=True)
                if not text or NOT_YET_GRADED_RE.match(text):
                    grades[label] = None
                else:
                    grades[label] = text

            data.courses.append(
                PowerSchoolCourseRow(
                    course_name=course_name,
                    teacher_name=teacher_name,
                    grades=grades,
                    absences=absences_cell.get_text(strip=True) or None,
                    tardies=tardies_cell.get_text(strip=True) or None,
                )
            )

        gpa_match = GPA_RE.search(soup.get_text(" ", strip=True))
        if gpa_match:
            data.gpa = gpa_match.group(1)

        return data

    @staticmethod
    def _parse_missing_assignments(html: str) -> list[MissingAssignment]:
        soup = BeautifulSoup(html, "html.parser")
        expected_header = ["course", "due date", "assignment", "category", "teacher"]

        table = None
        for candidate in soup.find_all("table"):
            first_row = candidate.find("tr")
            if not first_row:
                continue
            header_texts = [c.get_text(strip=True).lower() for c in first_row.find_all(["th", "td"])]
            if header_texts[: len(expected_header)] == expected_header:
                table = candidate
                break

        if table is None:
            # Not every student has a missing-assignments table at all
            # (e.g. nothing missing right now) -- that's not an error.
            return []

        assignments: list[MissingAssignment] = []
        rows = table.find_all("tr")[1:]  # skip header
        for row in rows:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            assignments.append(
                MissingAssignment(
                    course_name=cells[0].get_text(strip=True),
                    due_date=cells[1].get_text(strip=True) or None,
                    assignment_name=cells[2].get_text(strip=True) or None,
                    category=cells[3].get_text(strip=True) or None,
                    teacher_name=cells[4].get_text(strip=True) or None,
                )
            )
        return assignments
