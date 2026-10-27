"""Shared setup for the application tests (#93, applications R1–R21).

The template is fictional and club-neutral ("Example Club", "City Rink"),
its texts as typed; ``tests/test_application_fact_gaps.py`` adds gaps.
"""

import io
import json
from datetime import date
from typing import Any

from httpx import AsyncClient, Response
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from club_server.club_calendar import date_to_day
from club_server.db.models.user import User, UserStatus

from .helpers import (
    attach_identity_document,
    create_member_user,
    create_user_with_status,
)

APPLICATIONS = "/v1/applications"
PDF = "application/pdf"
BORN_UNDER_12 = date(2015, 5, 14)


def day(y: int, m: int, d: int) -> int:
    """A calendar date as the API sends it: UTC midnight, ms."""
    return date_to_day(date(y, m, d))


def auth(token: str) -> dict[str, str]:
    """Bearer header for ``token``."""
    return {"Authorization": f"Bearer {token}"}


def template_body() -> dict[str, Any]:
    """A complete English + Marathi template, as an admin would send it.

    Every printed text is custom text, here without gaps (applications R6),
    so its facts print nowhere until a test adds a gap naming one.
    """
    return {
        "languages": ["en", "mr"],
        "facts": {
            "venue": {"type": "name", "value": {"en": "City Rink", "mr": "सिटी रिंक"}},
            "phone": {"type": "name", "value": "98765 43210"},
            "camp": {
                "type": "date_range",
                "from": day(2026, 11, 16),
                "to": day(2026, 11, 17),
            },
            "matches": {
                "type": "date_range",
                "from": day(2026, 11, 19),
                "to": day(2026, 11, 21),
            },
        },
        "organisation": {"en": "EXAMPLE CLUB", "mr": "उदाहरण क्लब"},
        "title": {
            "en": "Selection Trial — Registration Form",
            "mr": "निवड चाचणी — नोंदणी अर्ज",
        },
        "subtitle": {"en": "16–17 November 2026", "mr": "16 ते 17 नोव्हेंबर 2026"},
        "header": [
            {"en": "City Rink", "mr": "सिटी रिंक"},
            [
                {
                    "en": "Training camp: 16–17 November 2026",
                    "mr": "प्रशिक्षण शिबिर: 16 ते 17 नोव्हेंबर 2026",
                },
                {
                    "en": "Matches: 19–21 November 2026",
                    "mr": "सामने: 19 ते 21 नोव्हेंबर 2026",
                },
            ],
            {"en": "98765 43210"},
        ],
        "ageCategories": [
            {
                "label": {"en": "Under-12", "mr": "अंडर-12"},
                "bornOnOrAfterUtc": day(2014, 1, 1),
            },
            {
                "label": {"en": "Under-15 Girls", "mr": "अंडर-15 मुली"},
                "bornOnOrAfterUtc": day(2011, 1, 1),
                "genders": ["female"],
            },
        ],
        "selections": [
            {
                "en": "I will attend the training camp from 16–17 November 2026.",
                "mr": "मी 16 ते 17 नोव्हेंबर 2026 दरम्यानच्या प्रशिक्षण शिबिराला उपस्थित राहीन.",
            }
        ],
        "declaration": {
            "en": "I hereby declare that the information provided above is true "
            "and correct. I agree to abide by all the rules and regulations of "
            "the selection trial.",
            "mr": "मी याची खात्री देतो/देते की वरील सर्व माहिती अचूक व खरी आहे आणि मी "
            "निवड चाचणीतील सर्व नियम व अटींचे पालन करीन.",
        },
        "formFooter": {"en": "Play fair, play hard.", "mr": "खिलाडूवृत्तीने खेळा."},
        "terms": [
            {
                "heading": {"en": "Identity Proof Required", "mr": "ओळखपत्र आवश्यक"},
                "body": {
                    "en": "All players must carry a valid Aadhaar Card or any "
                    "government-issued document bearing their date of birth and "
                    "address. This is mandatory for participation.",
                    "mr": "सर्व खेळाडूंनी आधार कार्ड किंवा जन्मतारीख व पत्ता असलेले वैध "
                    "कागदपत्र सोबत आणणे अनिवार्य आहे.",
                },
            },
            {
                "heading": {"en": "Matches", "mr": "सामने"},
                "body": {
                    "en": "Tournament matches will take place from 19–21 November "
                    "2026 at City Rink.",
                    "mr": "19 ते 21 नोव्हेंबर 2026 दरम्यान सिटी रिंक येथे स्पर्धेचे सामने होतील.",
                },
            },
            {
                "heading": {"en": "Kit", "mr": "साहित्य"},
                "body": {"en": "Bring your own helmet.", "mr": "स्वतःचे हेल्मेट आणा."},
            },
        ],
        "termsNotes": [
            {
                "text": {
                    "en": "All participants are expected to strictly follow the "
                    "above rules and regulations.",
                    "mr": "सर्वांनी वरील नियमांचे काटेकोर पालन करावे.",
                }
            },
            {
                "text": {
                    "en": "Discipline, Punctuality and Preparation — Keys to Success",
                    "mr": "शिस्त, वेळेचे पालन आणि तयारी हीच यशाची गुरुकिल्ली आहे",
                },
                "attribution": {"en": "— Example Club"},
            },
        ],
        "labelOverrides": {
            "field.past_experience": {
                "en": "Past experience — any skating sport",
                "mr": "पूर्व अनुभव",
            }
        },
    }


async def create_application(
    client: AsyncClient, admin: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """POST a template and return it."""
    response = await client.post(
        APPLICATIONS,
        json=template_body() if body is None else body,
        headers=auth(admin),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def move(
    client: AsyncClient, admin: str, application_id: int, action: str
) -> Response:
    """One state move."""
    return await client.post(
        f"{APPLICATIONS}/{application_id}/{action}", headers=auth(admin)
    )


async def published_application(
    client: AsyncClient, admin: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    """A template saved and published."""
    created = await create_application(client, admin, body)
    for action in ("save", "publish"):
        response = await move(client, admin, created["id"], action)
        assert response.status_code == 200, response.text
    return response.json()  # pyright: ignore[reportPossiblyUnboundVariable]


async def get_application(
    client: AsyncClient, token: str, application_id: int
) -> Response:
    """GET one template."""
    return await client.get(f"{APPLICATIONS}/{application_id}", headers=auth(token))


async def create_applicant(
    db_session: AsyncSession,
    username: str,
    *,
    born: date | None = BORN_UNDER_12,
    gender: str | None = "male",
    status: UserStatus = UserStatus.active,
    identity_document: bool = True,
    guardian: dict[str, str] | None = None,
) -> str:
    """A member with a full name, date of birth, gender and, by default, an
    identity document; return their token."""
    if status == UserStatus.active:
        token = await create_member_user(db_session, username)
    else:
        token = await create_user_with_status(db_session, username, status)
    user = await db_session.get(User, username)
    assert user is not None
    user.first_name, user.middle_name, user.last_name = "Ravi", "Kumar", "Sharma"
    user.date_of_birth = date_to_day(born) if born else None
    user.gender = gender
    user.guardian = json.dumps(guardian) if guardian else None
    await db_session.flush()
    if identity_document:
        _ = await attach_identity_document(db_session, username)
    return token


def pdf_text(content: bytes) -> str:
    """Every page's text, whitespace collapsed."""
    return " ".join(page_texts(content))


def page_texts(content: bytes) -> list[str]:
    """Each page's text, whitespace collapsed."""
    reader = PdfReader(io.BytesIO(content))
    return [" ".join((page.extract_text() or "").split()) for page in reader.pages]


def page_count(content: bytes) -> int:
    """How many pages a PDF has."""
    return len(PdfReader(io.BytesIO(content)).pages)


def assert_pdf(response: Response, application_id: int, username: str) -> str:
    """The response is a form PDF of the expected name; return its text."""
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == PDF
    assert (
        f'filename="application-{application_id}-{username}.pdf"'
        in response.headers["content-disposition"]
    )
    assert response.content.startswith(b"%PDF")
    return pdf_text(response.content)
