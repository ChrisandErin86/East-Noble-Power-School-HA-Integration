"""Unofficial PowerSchool guardian ("PCAS") portal client.

There is no public JSON API for parent/guardian accounts -- this scrapes the
same server-rendered HTML pages a browser would load. It was reverse
engineered against a live PowerSchool SIS instance on 2026-09-06 (assets
tagged mba-core 26.1) and confirmed against that district's real sign-in
form and grades table. Two things are district/version-specific and are the
first places to look if this breaks against a different school:

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
   <caption> text "Attendance By Class" and is a flat 16-column-per-row
   table: [0] expected-period, [1-5] last week Mon-Fri, [6-10] this week
   Mon-Fri, [11] course name + teacher link, [12] term 1 grade (linked to a
   scores.html detail page), [13] term 2 grade, [14] absences, [15] tardies.
   Column count/order can differ by district skin or PowerSchool version --
   if parsing comes back empty, log the raw table HTML and adjust
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
STUDENT_LINK_RE = re.compile(r"switchStudent\((\d+)\)")
GPA_RE = re.compile(r"GPA\s*\(S\d\):\s*([\d.]+)")
GRADES_TABLE_CAPTION = "Attendance By Class"


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
    term1_grade: str | None
    term2_grade: str | None
    absences: str | None
    tardies: str | None


@dataclass
class PowerSchoolStudentData:
    student: PowerSchoolStudent
    courses: list[PowerSchoolCourseRow] = field(default_factory=list)
    gpa: str | None = None


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
        """Switch to this student's context and scrape their grades page."""
        if student.student_id:
            await self.async_switch_student(student.student_id)
        else:
            await self._ensure_login()
        async with self._session.get(f"{self._base_url}{GUARDIAN_HOME_PATH}") as resp:
            resp.raise_for_status()
            html = await resp.text()
        return self._parse_student_data(student, html)

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
        else:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) != 16:
                    continue  # header/spacer rows aren't course rows
                course_cell = cells[11]
                course_links = course_cell.find_all("a")
                teacher_name = None
                if course_links:
                    title = course_links[0].get("title", "")
                    teacher_name = title.replace("Details about ", "").strip() or None

                data.courses.append(
                    PowerSchoolCourseRow(
                        course_name=course_cell.get_text(" ", strip=True),
                        teacher_name=teacher_name,
                        term1_grade=cells[12].get_text(strip=True) or None,
                        term2_grade=cells[13].get_text(strip=True) or None,
                        absences=cells[14].get_text(strip=True) or None,
                        tardies=cells[15].get_text(strip=True) or None,
                    )
                )

        gpa_match = GPA_RE.search(soup.get_text(" ", strip=True))
        if gpa_match:
            data.gpa = gpa_match.group(1)

        return data
