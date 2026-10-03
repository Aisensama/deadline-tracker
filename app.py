import asyncio
import re
from datetime import date, datetime

import streamlit as st

from google import genai
from google.genai import types
from telegram import Bot

from prompts import (
    SYSTEM_PROMPT,
    SUMMARY_REQUEST_PROMPT,
    WELCOME_MESSAGE_TEMPLATE,
)


# ============================================================
# CONFIGURATION
# ============================================================

MODEL_NAME = "gemini-3.5-flash-lite"

st.set_page_config(
    page_title="Deadline Tracker",
    page_icon="📚",
    layout="wide",
)


# ============================================================
# SECRETS
# ============================================================

GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
TELEGRAM_BOT_TOKEN = st.secrets["TELEGRAM_BOT_TOKEN"]


# ============================================================
# GEMINI CLIENT
# ============================================================

@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=GEMINI_API_KEY)


gemini_client = get_gemini_client()


# ============================================================
# ONBOARDING
# ============================================================

if "onboarded" not in st.session_state:

    st.title("📚 Deadline Tracker")

    st.caption(
        "Turn syllabi, timetables, and assignment sheets "
        "into a clean deadline list."
    )

    with st.form("onboarding_form"):

        name = st.text_input(
            "Your name",
            placeholder="Enter your name",
        )

        telegram_chat_id = st.text_input(
            "Telegram Chat ID",
            placeholder="Enter your Telegram chat ID",
            help=(
                "Send /start to your Telegram bot, then use "
                "the chat ID you obtained."
            ),
        )

        submitted = st.form_submit_button(
            "Start Tracking 🚀",
            type="primary",
            use_container_width=True,
        )

        if submitted:

            clean_name = name.strip()
            clean_chat_id = telegram_chat_id.strip()

            if not clean_name:

                st.warning("Please enter your name.")

            elif not clean_chat_id:

                st.warning("Please enter your Telegram Chat ID.")

            elif not clean_chat_id.lstrip("-").isdigit():

                st.warning(
                    "Telegram Chat ID should contain numbers."
                )

            else:

                st.session_state.name = clean_name

                st.session_state.telegram_chat_id = (
                    clean_chat_id
                )

                st.session_state.chat = (
                    gemini_client.chats.create(
                        model=MODEL_NAME,
                        config=types.GenerateContentConfig(
                            system_instruction=SYSTEM_PROMPT
                        ),
                    )
                )

                st.session_state.messages = []

                st.session_state.deadlines = []

                st.session_state.last_status = None

                st.session_state.onboarded = True

                st.rerun()

    st.stop()


# ============================================================
# SESSION DEFAULTS
# ============================================================

st.session_state.setdefault(
    "messages",
    [],
)

st.session_state.setdefault(
    "deadlines",
    [],
)

st.session_state.setdefault(
    "last_status",
    None,
)


# ============================================================
# MESSAGE FUNCTIONS
# ============================================================

def add_message(role, kind, content):

    st.session_state.messages.append(
        {
            "role": role,
            "kind": kind,
            "content": content,
        }
    )


def render_message(message):

    with st.chat_message(message["role"]):

        if message["kind"] == "text":

            st.write(message["content"])

        elif message["kind"] == "image":

            st.image(
                message["content"],
                use_container_width=True,
            )


# ============================================================
# GEMINI
# ============================================================

def ask_gemini(parts):

    try:

        response = st.session_state.chat.send_message(
            parts
        )

        answer = (response.text or "").strip()

        if not answer:

            return False, "Gemini returned an empty response."

        return True, answer

    except Exception as error:

        return False, str(error)


# ============================================================
# DEADLINE PARSING
# ============================================================

def parse_deadlines(text):

    if not text:

        return []

    if "NO DEADLINES FOUND" in text.upper():

        return []

    blocks = re.split(
        r"(?=DEADLINE\s+\d+)",
        text,
        flags=re.IGNORECASE,
    )

    deadlines = []

    for block in blocks:

        if not block.strip():

            continue

        event_match = re.search(
            r"Event:\s*(.+?)(?=\s*Date:)",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

        date_match = re.search(
            r"Date:\s*(.+?)(?=\s*Time:)",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

        time_match = re.search(
            r"Time:\s*(.+?)(?=\s*Details:)",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

        details_match = re.search(
            r"Details:\s*(.+)",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if not event_match or not date_match:

            continue

        event = " ".join(
            event_match.group(1).split()
        )

        deadline_date = " ".join(
            date_match.group(1).split()
        )

        deadline_time = (
            " ".join(time_match.group(1).split())
            if time_match
            else "Unclear"
        )

        details = (
            " ".join(details_match.group(1).split())
            if details_match
            else "No additional details."
        )

        deadlines.append(
            {
                "event": event,
                "date": deadline_date,
                "time": deadline_time,
                "details": details,
            }
        )

    return deadlines


# ============================================================
# DUPLICATE DETECTION
# ============================================================

def normalize_text(value):

    return " ".join(
        value.strip().lower().split()
    )


def deadline_key(deadline):

    return (
        normalize_text(deadline["event"]),
        normalize_text(deadline["date"]),
    )


def add_unique_deadlines(new_deadlines):

    existing_by_key = {
        deadline_key(deadline): deadline
        for deadline in st.session_state.deadlines
    }

    added_count = 0
    updated_count = 0

    for new_deadline in new_deadlines:

        key = deadline_key(new_deadline)

        existing = existing_by_key.get(key)

        # New deadline
        if existing is None:

            st.session_state.deadlines.append(
                new_deadline
            )

            existing_by_key[key] = new_deadline

            added_count += 1

            continue

        # Improve an existing deadline if a later scan
        # provides information that was previously unclear.
        old_time = normalize_text(
            existing["time"]
        )

        new_time = normalize_text(
            new_deadline["time"]
        )

        if (
            old_time == "unclear"
            and new_time != "unclear"
        ):

            existing["time"] = new_deadline["time"]

            updated_count += 1

        old_details = normalize_text(
            existing["details"]
        )

        new_details = normalize_text(
            new_deadline["details"]
        )

        if (
            old_details in {
                "",
                "no additional details.",
            }
            and new_details not in {
                "",
                "no additional details.",
            }
        ):

            existing["details"] = (
                new_deadline["details"]
            )

            updated_count += 1

    return added_count, updated_count


# ============================================================
# DATE HANDLING
# ============================================================

def parse_date(date_text):

    cleaned = (
        date_text
        .replace(",", " ")
        .strip()
    )

    cleaned = " ".join(
        cleaned.split()
    )

    formats = [
        "%d %B %Y",
        "%d %b %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y-%m-%d",
    ]

    for fmt in formats:

        try:

            return datetime.strptime(
                cleaned,
                fmt,
            ).date()

        except ValueError:

            pass

    return None


def sort_deadlines(deadlines):

    def sort_key(item):

        parsed = parse_date(
            item["date"]
        )

        return (
            parsed is None,
            parsed or date.max,
        )

    return sorted(
        deadlines,
        key=sort_key,
    )


def get_status(date_text):

    parsed = parse_date(
        date_text
    )

    if parsed is None:

        return "Date unclear"

    days_remaining = (
        parsed - date.today()
    ).days

    if days_remaining < 0:

        return "Past"

    if days_remaining == 0:

        return "Today"

    if days_remaining == 1:

        return "Tomorrow"

    return f"{days_remaining} days"


# ============================================================
# TELEGRAM
# ============================================================

def clean_telegram_text(text):

    if not text:

        text = "No deadline summary available."

    text = text.strip()

    # Telegram text limit is 4096 characters.
    # Keep a little safety margin.
    if len(text) > 4000:

        text = text[:3997] + "..."

    return text


def send_telegram(chat_id, text):

    async def send():

        async with Bot(
            token=TELEGRAM_BOT_TOKEN
        ) as bot:

            message = await bot.send_message(
                chat_id=int(chat_id),
                text=clean_telegram_text(text),
            )

            return message.message_id

    try:

        message_id = asyncio.run(
            send()
        )

        return True, message_id

    except Exception as error:

        return False, str(error)


# ============================================================
# TELEGRAM SUMMARY
# ============================================================

def build_deadline_source_text():

    deadlines = sort_deadlines(
        st.session_state.deadlines
    )

    lines = []

    for index, deadline in enumerate(
        deadlines,
        start=1,
    ):

        lines.append(
            f"{index}. "
            f"Event: {deadline['event']} | "
            f"Date: {deadline['date']} | "
            f"Time: {deadline['time']} | "
            f"Details: {deadline['details']}"
        )

    return "\n".join(lines)


def fallback_telegram_summary():

    deadlines = sort_deadlines(
        st.session_state.deadlines
    )

    lines = [
        f"📚 {st.session_state.name}'s Deadline Tracker",
        "",
        "Academic deadlines:",
        "",
    ]

    for deadline in deadlines:

        lines.append(
            f"📝 {deadline['event']}"
        )

        lines.append(
            f"📅 {deadline['date']}"
        )

        if deadline["time"]:

            lines.append(
                f"⏰ {deadline['time']}"
            )

        if deadline["details"]:

            lines.append(
                f"📌 {deadline['details']}"
            )

        lines.append("")

    return "\n".join(
        lines
    ).strip()


def create_telegram_summary():

    source_data = (
        build_deadline_source_text()
    )

    summary_prompt = f"""
Create a concise Telegram-friendly deadline digest
from the tracked academic deadlines below.

Rules:
- Use ONLY the tracked records.
- Do not invent information.
- Do not change dates.
- Do not change times.
- Do not add deadlines.
- Do not duplicate deadlines.
- Keep each deadline easy to scan.
- Use simple plain text.
- Use emojis sparingly.
- Preserve unclear information as "Unclear".

Tracked deadlines:

{source_data}
"""

    success, result = ask_gemini(
        [summary_prompt]
    )

    if success:

        return result

    return fallback_telegram_summary()


# ============================================================
# PROCESS CHAT INPUT FIRST
# ============================================================
#
# IMPORTANT:
# The input must be processed BEFORE rendering the
# deadline dashboard and Telegram button.
#
# This prevents the stale-state problem we encountered earlier.
# ============================================================

user_input = st.chat_input(
    "Ask a question, or attach a syllabus/timetable/assignment...",
    accept_file=True,
    file_type=[
        "jpg",
        "jpeg",
        "png",
    ],
)


if user_input:

    photo = (
        user_input.files[0]
        if user_input.files
        else None
    )

    text = (
        user_input.text or ""
    ).strip()

    parts = []


    # --------------------------------------------------------
    # IMAGE
    # --------------------------------------------------------

    if photo is not None:

        photo_bytes = photo.getvalue()

        add_message(
            "user",
            "image",
            photo_bytes,
        )

        parts.append(
            types.Part.from_bytes(
                data=photo_bytes,
                mime_type=photo.type,
            )
        )


    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    if text:

        add_message(
            "user",
            "text",
            text,
        )

        parts.append(text)


    # --------------------------------------------------------
    # IMAGE WITH NO CAPTION
    # --------------------------------------------------------

    elif photo is not None:

        parts.append(
            """
Analyze this academic document.

Identify every clearly recognizable:
- assignment
- quiz
- exam
- project
- project milestone
- submission
- deadline
- important academic event

Follow the structured output rules from the system prompt.

Do not invent dates or times.
"""
        )


    # --------------------------------------------------------
    # EMPTY INPUT
    # --------------------------------------------------------

    if not parts:

        st.session_state.last_status = (
            "Please enter text or attach an image."
        )

    else:

        with st.spinner(
            "Analyzing with Gemini..."
        ):

            success, answer = ask_gemini(
                parts
            )


        if success:

            add_message(
                "assistant",
                "text",
                answer,
            )


            # --------------------------------------------
            # PARSE STRUCTURED DEADLINES
            # --------------------------------------------

            extracted = parse_deadlines(
                answer
            )


            if extracted:

                added_count, updated_count = (
                    add_unique_deadlines(
                        extracted
                    )
                )

                messages = []

                if added_count:

                    messages.append(
                        f"Added {added_count} new deadline(s)."
                    )

                if updated_count:

                    messages.append(
                        f"Updated {updated_count} existing field(s)."
                    )

                if not messages:

                    messages.append(
                        "These deadlines are already tracked."
                    )

                st.session_state.last_status = (
                    " ".join(messages)
                )

            elif (
                "NO DEADLINES FOUND"
                in answer.upper()
            ):

                st.session_state.last_status = (
                    "No recognizable academic deadlines "
                    "were found in this document."
                )

            else:

                st.session_state.last_status = (
                    "No structured deadlines were detected "
                    "in this response."
                )


        else:

            add_message(
                "assistant",
                "text",
                (
                    "Sorry, something went wrong:\n\n"
                    f"{answer}"
                ),
            )

            st.session_state.last_status = None


# ============================================================
# MAIN HEADER
# ============================================================

st.title("📚 Deadline Tracker")

st.caption(
    f"Welcome, {st.session_state.name}. "
    "Keep your academic deadlines in one place."
)


# ============================================================
# SIDEBAR
# ============================================================

with st.sidebar:

    st.header("⚙️ Session")

    st.write(
        f"**Name:** {st.session_state.name}"
    )

    st.write(
        "**Telegram Chat ID:** "
        f"`{st.session_state.telegram_chat_id}`"
    )

    st.divider()

    st.caption(
        "Gemini analyzes your academic documents. "
        "Telegram receives your final digest."
    )

    if st.button(
        "🔄 New Session",
        use_container_width=True,
        key="new_session_button",
    ):

        st.session_state.clear()

        st.rerun()


# ============================================================
# STATUS MESSAGE
# ============================================================

if st.session_state.last_status:

    st.info(
        st.session_state.last_status
    )

    st.session_state.last_status = None


# ============================================================
# DEADLINE DASHBOARD
# ============================================================

deadlines = sort_deadlines(
    st.session_state.deadlines
)


st.divider()

st.subheader("📅 Deadline Dashboard")


if deadlines:

    upcoming = []

    for deadline in deadlines:

        parsed = parse_date(
            deadline["date"]
        )

        if (
            parsed is not None
            and parsed >= date.today()
        ):

            upcoming.append(
                deadline
            )


    metric1, metric2, metric3 = st.columns(3)


    with metric1:

        st.metric(
            "Tracked",
            len(deadlines),
        )


    with metric2:

        st.metric(
            "Upcoming",
            len(upcoming),
        )


    with metric3:

        next_date = (
            upcoming[0]["date"]
            if upcoming
            else "None"
        )

        st.metric(
            "Next deadline",
            next_date,
        )


    st.write("")


    # --------------------------------------------------------
    # DEADLINE CARDS
    # --------------------------------------------------------

    for deadline in deadlines:

        status = get_status(
            deadline["date"]
        )

        with st.container(
            border=True
        ):

            left_col, right_col = st.columns(
                [5, 1],
                vertical_alignment="center",
            )


            with left_col:

                st.markdown(
                    f"### 📝 {deadline['event']}"
                )

                st.write(
                    f"📅 **Date:** "
                    f"{deadline['date']}"
                )

                st.write(
                    f"⏰ **Time:** "
                    f"{deadline['time']}"
                )

                st.write(
                    f"📌 **Details:** "
                    f"{deadline['details']}"
                )


            with right_col:

                st.metric(
                    "Status",
                    status,
                )


    # --------------------------------------------------------
    # ACTIONS
    # --------------------------------------------------------

    st.divider()

    send_col, clear_col = st.columns(
        [3, 1]
    )


    with send_col:

        if st.button(
            "📤 Send Deadline Digest to Telegram",
            type="primary",
            use_container_width=True,
            key="send_deadline_digest_button",
        ):

            with st.spinner(
                "Preparing your Telegram digest..."
            ):

                summary = (
                    create_telegram_summary()
                )


            with st.spinner(
                "Sending to Telegram..."
            ):

                success, info = send_telegram(
                    st.session_state.telegram_chat_id,
                    summary,
                )


            if success:

                st.success(
                    "Deadline digest sent to Telegram 📲"
                )

            else:

                st.error(
                    f"Telegram send failed: {info}"
                )


    with clear_col:

        if st.button(
            "🗑️ Clear",
            use_container_width=True,
            key="clear_deadlines_button",
        ):

            st.session_state.deadlines = []

            st.rerun()


else:

    st.info(
        "No deadlines tracked yet. "
        "Upload a syllabus, timetable, assignment sheet, "
        "or academic notice below."
    )


# ============================================================
# CHAT HISTORY
# ============================================================

st.divider()

st.subheader("💬 AI Assistant")


if not st.session_state.messages:

    add_message(
        "assistant",
        "text",
        WELCOME_MESSAGE_TEMPLATE.format(
            name=st.session_state.name
        ),
    )


for message in st.session_state.messages:

    render_message(message)


# ============================================================
# FOOTER
# ============================================================

st.divider()

st.caption(
    "Deadline Tracker • Gemini Vision • Telegram"
)