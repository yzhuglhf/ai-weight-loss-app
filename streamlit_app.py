import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from app.db import (
    init_db, get_conn,
    get_or_create_default_user,
    get_usage_today, increment_usage,
    get_profile, save_profile,
    save_meal_plan,
    activate_meal_plan, get_planned_meals, mark_planned_meal_done,
)
from app.services.ai import chat, generate_meal_plan, analyze_food_image, analyze_food_text, estimate_calories

init_db()

st.set_page_config(page_title="NutriCoach", layout="centered")

st.markdown(
    """
    <style>
    /* Hide CookieManager iframe */
    iframe[title="extra_streamlit_components.CookieManager"] {
        display: none !important;
        height: 0 !important;
    }

    /* ── Base spacing ─────────────────────────────────────── */
    .block-container {
        padding-top: 1.25rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 860px !important;
    }

    /* ── Prevent iOS auto-zoom on input focus ─────────────── */
    input, textarea, select,
    [data-baseweb="input"] input,
    [data-baseweb="textarea"] textarea,
    [data-baseweb="select"] input {
        font-size: 16px !important;
    }

    /* ── Larger tap targets for buttons ───────────────────── */
    .stButton > button {
        min-height: 44px !important;
        font-size: 15px !important;
    }

    /* ── Scrollable tab bar (no overflow clip) ────────────── */
    [data-baseweb="tab-list"] {
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch !important;
        scrollbar-width: none !important;
        flex-wrap: nowrap !important;
        gap: 2px !important;
    }
    [data-baseweb="tab-list"]::-webkit-scrollbar { display: none; }
    [data-baseweb="tab"] {
        white-space: nowrap !important;
        padding: 10px 14px !important;
        font-size: 14px !important;
    }

    /* ── Stack columns on small screens ──────────────────── */
    @media (max-width: 640px) {
        .block-container {
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        [data-testid="stHorizontalBlock"] {
            flex-wrap: wrap !important;
        }
        [data-testid="column"] {
            width: 100% !important;
            flex: 1 1 100% !important;
            min-width: 100% !important;
        }
        /* Keep 2-button rows side by side */
        [data-testid="column"]:has(> div > [data-testid="stButton"]:only-child) {
            flex: 1 1 45% !important;
            min-width: 45% !important;
        }
        .stMetric { font-size: 13px; }
        [data-baseweb="tab"] {
            padding: 8px 10px !important;
            font-size: 13px !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

DAILY_AI_LIMIT = int(os.getenv("DAILY_AI_LIMIT", "20"))


def _check_rate_limit(user_id: int) -> bool:
    """Show an error and return False if the user has hit their daily limit."""
    used = get_usage_today(user_id)
    if used >= DAILY_AI_LIMIT:
        st.error(
            f"Daily AI limit of {DAILY_AI_LIMIT} requests reached. "
            "Come back tomorrow or ask the admin to raise your limit."
        )
        return False
    return True


def _parse_meal_plan(text: str) -> list[dict]:
    """Extract structured meals from a generated plan. Returns [{day, type, name, calories}].

    Handles common AI output variations:
    - Day headers: **Day 1**, **Day 1:**, ### Day 1, **Today**
    - Meal prefixes: - Breakfast:, * Breakfast:, **Breakfast:**
    - Calorie formats: (~350 cal), ~350 cal, (~350 calories), (350 kcal)
    """
    meals, day = [], 1
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # Day header — flexible: **Day 2**, ### Day 2, Day 2:, **Today**
        dm = re.search(r"\bDay\s+(\d+)\b", line, re.IGNORECASE)
        if dm:
            day = int(dm.group(1))
            continue
        if re.search(r"\bToday\b", line, re.IGNORECASE) and re.search(r"\*\*|^#+\s", line):
            day = 1
            continue

        # Skip summary / total lines
        if re.search(r"\b(daily\s+total|total\s+cal)\b", line, re.IGNORECASE):
            continue

        # Meal type — allow bold markers and optional colon/space variations
        type_m = re.search(
            r"\*{0,2}(Breakfast|Lunch|Dinner|Snacks?|Morning\s+Snack|Afternoon\s+Snack)\*{0,2}"
            r"[\s:*]+",
            line, re.IGNORECASE,
        )
        if not type_m:
            continue

        # Calorie number — (~350 cal), ~350 calories, (350 kcal), 350 cal
        cal_m = re.search(r"[~(]?\s*(\d[\d,]*)\s*(?:cal(?:ories)?|kcal)", line, re.IGNORECASE)
        if not cal_m:
            continue

        # Meal name: text between the type label and the calorie marker
        after_type = line[type_m.end():]
        cal_pos = after_type.find(cal_m.group(0))
        name = (after_type[:cal_pos] if cal_pos > 0 else after_type).strip().strip("(~").strip()
        if not name:
            continue

        meals.append({
            "day": day,
            "type": type_m.group(1).strip("*").capitalize(),
            "name": name,
            "calories": int(cal_m.group(1).replace(",", "")),
        })
    return meals


def _extract_scan_result(result: str) -> None:
    """Store analysis result and parse out the calorie number into session state."""
    st.session_state["scan_result"] = result
    cal_hint = 0
    for line in result.splitlines():
        if "estimated calories" in line.lower():
            nums = re.findall(r"\d+", line)
            if nums:
                cal_hint = int(nums[0])
            break
    st.session_state["scan_calories"] = cal_hint


ACTIVITY_LEVELS = [
    "Sedentary",
    "Lightly active",
    "Moderately active",
    "Very active",
    "Extra active",
]
ACTIVITY_DESC = {
    "Sedentary": "desk job, little/no exercise",
    "Lightly active": "light exercise 1–3 days/week",
    "Moderately active": "moderate exercise 3–5 days/week",
    "Very active": "hard exercise 6–7 days/week",
    "Extra active": "physical job + daily training",
}


def _calc_tdee(profile, weight_lbs: float) -> int | None:
    if not profile:
        return None
    age, gender, height_cm, activity = (
        profile["age"], profile["gender"], profile["height_cm"], profile["activity_level"]
    )
    if not all([age, gender, height_cm, activity]):
        return None
    w_kg = weight_lbs / 2.20462
    if gender == "Male":
        bmr = 10 * w_kg + 6.25 * height_cm - 5 * age + 5
    elif gender == "Female":
        bmr = 10 * w_kg + 6.25 * height_cm - 5 * age - 161
    else:
        bmr = 10 * w_kg + 6.25 * height_cm - 5 * age - 78
    mult = {"Sedentary": 1.2, "Lightly active": 1.375, "Moderately active": 1.55,
            "Very active": 1.725, "Extra active": 1.9}[activity]
    return round(bmr * mult)


def _calc_bmi(weight_lbs: float, height_cm: float) -> float:
    return round((weight_lbs / 2.20462) / (height_cm / 100) ** 2, 1)


def _bmi_label(bmi: float) -> str:
    if bmi < 18.5: return "Underweight"
    if bmi < 25.0: return "Normal weight"
    if bmi < 30.0: return "Overweight"
    return "Obese"


# ── Auto-login (auth disabled for private use) ────────────────────────────────
if not st.session_state.get("user_id"):
    st.session_state["user_id"] = get_or_create_default_user()


# ── Sidebar ───────────────────────────────────────────────────────────────────
uid = st.session_state["user_id"]
profile = get_profile(uid)

with st.sidebar:
    # Today's calorie progress
    with get_conn() as _sb:
        _sb_cal = (_sb.execute(
            "SELECT COALESCE(SUM(calories),0) FROM calorie_logs WHERE user_id=? AND date=?",
            (uid, str(date.today()))
        ).fetchone()[0])
    with get_conn() as _sbw:
        _sbw_lw = _sbw.execute(
            "SELECT weight_lbs FROM weight_logs WHERE user_id=? ORDER BY date DESC LIMIT 1", (uid,)
        ).fetchone()
    _sb_target = ((_calc_tdee(profile, _sbw_lw["weight_lbs"]) or 2300) - 500) if (_sbw_lw and profile) else None
    if _sb_target:
        _sb_remaining = _sb_target - _sb_cal
        _sb_label = f"✅ {_sb_remaining} cal left" if _sb_remaining >= 0 else f"⚠️ {abs(_sb_remaining)} cal over"
        st.caption(f"Today: {_sb_cal:,} / {_sb_target:,} cal — {_sb_label}")
        st.progress(min(_sb_cal / _sb_target, 1.0))
    elif _sb_cal:
        st.caption(f"Today: {_sb_cal:,} cal logged")

    used_today = get_usage_today(uid)
    st.caption(f"AI requests today: {used_today} / {DAILY_AI_LIMIT}")
    st.progress(min(used_today / DAILY_AI_LIMIT, 1.0))

st.title("NutriCoach")

# Restore the active tab after a rerun (set _goto_tab before calling st.rerun())
if "_goto_tab" in st.session_state:
    _tidx = st.session_state.pop("_goto_tab")
    components.html(
        f"""<script>
        setTimeout(function(){{
            var t=window.parent.document.querySelectorAll('[data-baseweb="tab"]');
            if(t&&t.length>{_tidx})t[{_tidx}].click();
        }},120);
        </script>""",
        height=0,
    )

tab_ai, tab_calories, tab_weight, tab_planner, tab_scanner, tab_profile = st.tabs(
    ["AI Coach", "Calories", "Weight", "Meal Plan", "Scanner", "Profile"]
)


# ── Tab 1: Profile ───────────────────────────────────────────────────────────
with tab_profile:
    if not profile:
        st.info("Complete your profile so the AI can give personalised advice and auto-calculate your calorie target.")

    col_form, col_stats = st.columns([1, 1])

    with col_form:
        st.subheader("Personal Info")

        # Unit toggles must be OUTSIDE the form so switching triggers an immediate rerun
        h_unit = st.radio("Height unit", ["cm", "ft + in"], horizontal=True, key="prof_h_unit")
        g_unit = st.radio("Goal weight unit", ["lbs", "kg"], horizontal=True, key="prof_g_unit")

        with st.form("profile_form"):
            p_name    = st.text_input("Name (optional)", value=profile["name"] or "" if profile else "")
            p_age     = st.number_input("Age", min_value=10, max_value=120, step=1,
                                        value=int(profile["age"]) if profile and profile["age"] else 25)
            p_gender  = st.selectbox("Gender",
                                     ["Male", "Female", "Prefer not to say"],
                                     index=["Male","Female","Prefer not to say"].index(profile["gender"])
                                     if profile and profile["gender"] else 0)

            if h_unit == "cm":
                p_height_cm = st.number_input("Height (cm)", min_value=100.0, max_value=250.0, step=0.5,
                                              value=float(profile["height_cm"]) if profile and profile["height_cm"] else 170.0)
            else:
                _saved_cm = float(profile["height_cm"]) if profile and profile["height_cm"] else 170.18
                _def_ft   = int(_saved_cm // 30.48)
                _def_in   = round((_saved_cm % 30.48) / 2.54)
                h_ft = st.number_input("Feet",   min_value=3, max_value=8,  step=1, value=_def_ft)
                h_in = st.number_input("Inches", min_value=0, max_value=11, step=1, value=_def_in)
                p_height_cm = round((h_ft * 12 + h_in) * 2.54, 1)

            st.subheader("Your Goal")
            if g_unit == "lbs":
                p_goal_raw = st.number_input("Goal weight (lbs)", min_value=50.0, max_value=600.0, step=0.5,
                                             value=float(profile["goal_weight_lbs"]) if profile and profile["goal_weight_lbs"] else 150.0)
                p_goal_lbs = p_goal_raw
            else:
                _goal_kg_default = round(float(profile["goal_weight_lbs"]) / 2.20462, 1) if profile and profile["goal_weight_lbs"] else 68.0
                p_goal_raw = st.number_input("Goal weight (kg)", min_value=20.0, max_value=300.0, step=0.5,
                                             value=_goal_kg_default)
                p_goal_lbs = round(p_goal_raw * 2.20462, 2)

            p_target = st.date_input("Target date (optional)", value=None, min_value=date.today())

            st.subheader("Activity & Food")
            p_activity = st.selectbox(
                "Activity level",
                ACTIVITY_LEVELS,
                format_func=lambda x: f"{x} — {ACTIVITY_DESC[x]}",
                index=ACTIVITY_LEVELS.index(profile["activity_level"]) if profile and profile["activity_level"] else 1,
            )
            p_prefs    = st.text_input("Dietary preferences",
                                       value=profile["dietary_prefs"] or "" if profile else "",
                                       placeholder="e.g. vegetarian, low-carb")
            p_allergies = st.text_input("Allergies / foods to avoid",
                                        value=profile["allergies"] or "" if profile else "",
                                        placeholder="e.g. nuts, dairy")

            saved = st.form_submit_button("Save Profile", type="primary")

        if saved:
            save_profile(
                uid,
                name=p_name.strip() or None,
                age=int(p_age),
                gender=p_gender,
                height_cm=p_height_cm,
                goal_weight_lbs=p_goal_lbs,
                target_date=str(p_target) if p_target else None,
                activity_level=p_activity,
                dietary_prefs=p_prefs.strip() or None,
                allergies=p_allergies.strip() or None,
            )
            st.success("Profile saved!")
            profile = get_profile(uid)
            st.session_state["_goto_tab"] = 5
            st.rerun()

    with col_stats:
        st.subheader("Your Stats")
        with get_conn() as conn:
            latest_w = conn.execute(
                "SELECT weight_lbs FROM weight_logs WHERE user_id = ? ORDER BY date DESC LIMIT 1",
                (uid,),
            ).fetchone()
        current_lbs = latest_w["weight_lbs"] if latest_w else None

        if profile and current_lbs:
            tdee = _calc_tdee(profile, current_lbs)
            if tdee:
                loss_target = tdee - 500
                st.metric("Maintenance calories (TDEE)", f"{tdee:,} cal/day")
                st.metric("Recommended for ~1 lb/week loss", f"{loss_target:,} cal/day")

            if profile["height_cm"]:
                bmi = _calc_bmi(current_lbs, profile["height_cm"])
                label = _bmi_label(bmi)
                st.metric("BMI", f"{bmi} — {label}")

            if profile["goal_weight_lbs"] and current_lbs:
                diff = current_lbs - profile["goal_weight_lbs"]
                if diff > 0:
                    weeks = round(diff / 1, 0)
                    st.metric("Weight to lose", f"{diff:.1f} lbs",
                              f"~{int(weeks)} weeks at 1 lb/week")
                else:
                    st.success("You have reached your goal weight!")
        else:
            st.info("Log your weight and complete your profile to see personalised stats here.")


# ── Tab 1: AI Coach ───────────────────────────────────────────────────────────
with tab_ai:
    # Compute today's calorie context for the AI
    with get_conn() as _ac:
        _ai_cal_today = _ac.execute(
            "SELECT COALESCE(SUM(calories),0) FROM calorie_logs WHERE user_id=? AND date=?",
            (uid, str(date.today()))
        ).fetchone()[0]
    with get_conn() as _aw:
        _aw_lw = _aw.execute(
            "SELECT weight_lbs FROM weight_logs WHERE user_id=? ORDER BY date DESC LIMIT 1", (uid,)
        ).fetchone()
    _ai_target = ((_calc_tdee(profile, _aw_lw["weight_lbs"]) or 2300) - 500) if (_aw_lw and profile) else None
    _ai_remaining = (_ai_target - _ai_cal_today) if _ai_target else None

    today_stats_str = ""
    if _ai_target:
        today_stats_str = (
            f"Today's nutrition snapshot: {_ai_cal_today} cal logged, "
            f"daily target {_ai_target} cal, "
            f"{_ai_remaining} cal remaining."
        )
    elif _ai_cal_today:
        today_stats_str = f"Today's nutrition snapshot: {_ai_cal_today} cal logged so far."

    # Header row with clear button
    hdr_col, btn_col = st.columns([3, 1])
    name_str = (profile["name"] + "!" if profile and profile["name"] else "")
    hdr_col.subheader(f"Hi {name_str} I'm your AI Coach" if name_str else "AI Coach")
    if btn_col.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    if today_stats_str:
        st.caption(today_stats_str)

    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Handle quick prompt from buttons (set before rerun, processed here)
    pending_prompt = st.session_state.pop("quick_prompt", None)
    if pending_prompt:
        st.session_state.messages.append({"role": "user", "content": pending_prompt})

    # Quick-action prompts — shown only when conversation is fresh
    if not st.session_state.messages:
        st.markdown("**Quick actions — tap to ask:**")
        qp1, qp2 = st.columns(2)
        _prefs = profile["dietary_prefs"] if profile and profile["dietary_prefs"] else ""
        _goal_lbs = profile["goal_weight_lbs"] if profile and profile["goal_weight_lbs"] else None

        with qp1:
            if _ai_remaining is not None and _ai_remaining >= 0:
                _btn1_label = f"What can I eat? ({_ai_remaining:,} cal left)"
                _btn1_prompt = (
                    f"I have {_ai_remaining} calories left today "
                    f"(logged {_ai_cal_today} cal, target {_ai_target} cal). "
                    "Suggest 3 specific meal or snack options that fit my remaining budget, "
                    "with calorie counts for each."
                )
            else:
                _btn1_label = "Suggest a healthy meal"
                _btn1_prompt = "Suggest 3 healthy, balanced meal options with approximate calories for each."
            if st.button(_btn1_label, use_container_width=True):
                st.session_state["quick_prompt"] = _btn1_prompt
                st.rerun()

            if st.button("Healthy snack ideas", use_container_width=True):
                st.session_state["quick_prompt"] = (
                    f"Give me 5 healthy snack ideas under 200 calories each"
                    f"{', fitting ' + _prefs + ' preferences' if _prefs else ''}. "
                    "Include the approximate calories for each."
                )
                st.rerun()

        with qp2:
            if st.button("How am I progressing?", use_container_width=True):
                _wt_str = f"My current weight is {round(_aw_lw['weight_lbs'], 1)} lbs. " if _aw_lw else ""
                _goal_str = f"My goal weight is {_goal_lbs:.1f} lbs. " if _goal_lbs else ""
                st.session_state["quick_prompt"] = (
                    f"{_wt_str}{_goal_str}"
                    "Based on my profile and goals, give me an honest progress check "
                    "and 2-3 specific, actionable tips I can act on this week."
                )
                st.rerun()

            if st.button("Plan tomorrow's meals", use_container_width=True):
                _t = str(_ai_target) if _ai_target else "1800"
                st.session_state["quick_prompt"] = (
                    f"Help me plan tomorrow's meals targeting {_t} calories"
                    f"{'. Preferences: ' + _prefs if _prefs else ''}. "
                    "Give me breakfast, lunch, dinner, and a snack with approximate calories for each. "
                    "Keep it realistic and practical."
                )
                st.rerun()

        st.divider()

    # Render chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Auto-respond to a pending quick prompt
    if pending_prompt:
        with st.chat_message("assistant"):
            if _check_rate_limit(uid):
                with st.spinner("Thinking…"):
                    reply = chat(st.session_state.messages, profile=profile, today_stats=today_stats_str)
                increment_usage(uid)
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})

    if prompt := st.chat_input("Ask me anything about food, weight loss, or workouts…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            if _check_rate_limit(uid):
                with st.spinner("Thinking…"):
                    reply = chat(st.session_state.messages, profile=profile, today_stats=today_stats_str)
                increment_usage(uid)
                st.markdown(reply)
                st.session_state.messages.append({"role": "assistant", "content": reply})


# ── Tab 2: Weight Tracker ─────────────────────────────────────────────────────
with tab_weight:
    st.subheader("Track Your Weight")

    col_form, col_chart = st.columns([1, 2])

    with col_form:
        w_unit = st.radio("Unit", ["lbs", "kg"], horizontal=True, key="w_unit")
        w_date = st.date_input("Date", value=date.today(), key="w_date")

        if w_unit == "lbs":
            w_input = st.number_input("Weight (lbs)", min_value=50.0, max_value=1320.0, step=0.1, key="w_val")
            w_lbs = w_input
        else:
            w_input = st.number_input("Weight (kg)", min_value=20.0, max_value=600.0, step=0.1, key="w_val")
            w_lbs = round(w_input * 2.20462, 2)

        w_notes = st.text_input("Notes (optional)", key="w_notes")

        if st.button("Log Weight", type="primary"):
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO weight_logs (user_id, date, weight_lbs, notes) VALUES (?, ?, ?, ?)",
                    (uid, str(w_date), w_lbs, w_notes),
                )
                conn.commit()
            st.success(f"Logged {w_input:.1f} {w_unit} on {w_date}")
            st.session_state["_goto_tab"] = 2
            st.rerun()

    with col_chart:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, date, weight_lbs, notes FROM weight_logs WHERE user_id = ? ORDER BY date ASC",
                (uid,),
            ).fetchall()

        if rows:
            df = pd.DataFrame(rows, columns=["ID", "Date", "weight_lbs", "Notes"])
            df["Date"] = pd.to_datetime(df["Date"])

            if w_unit == "kg":
                df["Weight"] = (df["weight_lbs"] / 2.20462).round(1)
            else:
                df["Weight"] = df["weight_lbs"].round(1)

            display_col = f"Weight ({w_unit})"
            df = df.rename(columns={"Weight": display_col})

            start_w  = df[display_col].iloc[0]
            latest_w = df[display_col].iloc[-1]
            delta    = latest_w - start_w
            delta_str = f"{delta:+.1f} {w_unit} since start"

            c1, c2 = st.columns(2)
            c1.metric("Current Weight", f"{latest_w:.1f} {w_unit}", delta_str)
            c2.metric("Entries", len(df))

            st.line_chart(df.set_index("Date")[display_col])

            # ── Inline table with Edit / Delete per row ───────────────────────
            sorted_df = df.sort_values("Date", ascending=False).reset_index(drop=True)
            h = st.columns([3, 3, 1, 1])
            for col, label in zip(h, ["Date", f"Weight ({w_unit})", "", ""]):
                col.markdown(f"**{label}**")
            st.divider()

            for _, row in sorted_df.iterrows():
                row_id = int(row["ID"])
                c = st.columns([3, 3, 1, 1])
                c[0].write(row["Date"].strftime("%Y-%m-%d"))
                c[1].write(f"{row[display_col]:.1f}")
                if c[2].button("✏️", key=f"ew_open_{row_id}", help="Edit"):
                    st.session_state["editing_weight_id"] = row_id
                if c[3].button("✕", key=f"ew_del_{row_id}", help="Delete"):
                    with get_conn() as conn:
                        conn.execute(
                            "DELETE FROM weight_logs WHERE id = ? AND user_id = ?",
                            (row_id, uid),
                        )
                        conn.commit()
                    st.session_state.pop("editing_weight_id", None)
                    st.session_state["_goto_tab"] = 2
                    st.rerun()

            # ── Edit form (shown below the table when a row is being edited) ──
            editing_id = st.session_state.get("editing_weight_id")
            if editing_id and editing_id in sorted_df["ID"].values:
                edit_row = sorted_df[sorted_df["ID"] == editing_id].iloc[0]
                st.divider()
                st.markdown(f"**Editing entry — {edit_row['Date'].strftime('%Y-%m-%d')}**")
                with st.form("edit_weight_form"):
                    e_date = st.date_input("Date", value=edit_row["Date"].date())
                    e_input = st.number_input(
                        f"Weight ({w_unit})",
                        value=float(edit_row[display_col]),
                        min_value=20.0 if w_unit == "kg" else 50.0,
                        max_value=600.0 if w_unit == "kg" else 1320.0,
                        step=0.1,
                    )
                    e_notes = st.text_input("Notes", value=edit_row["Notes"] or "")
                    col_save, col_cancel = st.columns(2)
                    saved    = col_save.form_submit_button("Save", type="primary")
                    cancelled = col_cancel.form_submit_button("Cancel")

                if saved:
                    e_lbs = round(e_input * 2.20462, 2) if w_unit == "kg" else e_input
                    with get_conn() as conn:
                        conn.execute(
                            "UPDATE weight_logs SET date=?, weight_lbs=?, notes=? WHERE id=? AND user_id=?",
                            (str(e_date), e_lbs, e_notes, editing_id, uid),
                        )
                        conn.commit()
                    st.session_state.pop("editing_weight_id", None)
                    st.session_state["_goto_tab"] = 2
                    st.rerun()
                if cancelled:
                    st.session_state.pop("editing_weight_id", None)
                    st.session_state["_goto_tab"] = 2
                    st.rerun()
        else:
            st.info("No weight entries yet. Log your first entry on the left!")


# ── Tab 2: Calorie Log ────────────────────────────────────────────────────────
with tab_calories:
    st.subheader("Track Your Calories")

    # Daily goal progress bar
    with get_conn() as _cg:
        _cg_today = _cg.execute(
            "SELECT COALESCE(SUM(calories),0) FROM calorie_logs WHERE user_id=? AND date=?",
            (uid, str(date.today()))
        ).fetchone()[0]
    with get_conn() as _cgw:
        _cgw_lw = _cgw.execute(
            "SELECT weight_lbs FROM weight_logs WHERE user_id=? ORDER BY date DESC LIMIT 1", (uid,)
        ).fetchone()
    _cg_target = ((_calc_tdee(profile, _cgw_lw["weight_lbs"]) or 2300) - 500) if (_cgw_lw and profile) else None

    if _cg_target:
        _cg_remaining = _cg_target - _cg_today
        cg1, cg2, cg3 = st.columns(3)
        cg1.metric("Eaten today", f"{_cg_today:,} cal")
        cg2.metric("Daily target", f"{_cg_target:,} cal")
        if _cg_remaining >= 0:
            cg3.metric("Remaining", f"{_cg_remaining:,} cal")
        else:
            cg3.metric("Over target", f"{abs(_cg_remaining):,} cal")
        st.progress(min(_cg_today / _cg_target, 1.0))
        if _cg_remaining < 0:
            st.warning(f"You're {abs(_cg_remaining):,} cal over your daily target.")
        elif _cg_remaining <= 300:
            st.info(f"Only {_cg_remaining:,} cal left — choose your next meal carefully.")
    else:
        st.metric("Eaten today", f"{_cg_today:,} cal")
        if not profile:
            st.caption("Complete your Profile to see your daily calorie target here.")

    st.divider()
    col_form, col_chart = st.columns([1, 2])

    with col_form:
        # Quick-add from history
        with get_conn() as _qc:
            _frequent = _qc.execute(
                """SELECT meal_name, ROUND(AVG(calories)) as avg_cal, COUNT(*) as cnt
                   FROM calorie_logs WHERE user_id=?
                   GROUP BY meal_name ORDER BY cnt DESC LIMIT 6""",
                (uid,)
            ).fetchall()
        if _frequent:
            st.caption("**Quick add:**")
            for _fm in _frequent:
                _fc = int(_fm["avg_cal"])
                if st.button(f"{_fm['meal_name']}  ·  {_fc} cal", key=f"qa_{_fm['meal_name']}", use_container_width=True):
                    with get_conn() as _qconn:
                        _qconn.execute(
                            "INSERT INTO calorie_logs (user_id, date, meal_name, calories, notes) VALUES (?, ?, ?, ?, ?)",
                            (uid, str(date.today()), _fm["meal_name"], _fc, ""),
                        )
                        _qconn.commit()
                    st.success(f"Logged {_fm['meal_name']}: {_fc} cal")
                    st.session_state["_goto_tab"] = 1
                    st.rerun()
            st.divider()

        st.caption("**Log a meal:**")
        c_date = st.date_input("Date", value=date.today(), key="c_date")
        meal_name = st.text_input(
            "Food / Meal",
            key="c_meal",
            placeholder="e.g. 2 scrambled eggs with toast",
        )

        _meal_typed = st.session_state.get("c_meal", "").strip()
        if st.button("Estimate calories with AI", disabled=not _meal_typed, use_container_width=True):
            if _check_rate_limit(uid):
                with st.spinner("Estimating…"):
                    _est = estimate_calories(_meal_typed, profile=profile)
                increment_usage(uid)
                st.session_state["c_cals"] = _est
                st.session_state["_cal_est_label"] = f"AI estimated **{_est} cal** for \"{_meal_typed}\""
                st.rerun()

        if "_cal_est_label" in st.session_state:
            st.caption(st.session_state["_cal_est_label"])

        calories = st.number_input("Calories", min_value=0, max_value=5000, step=5, key="c_cals")
        c_notes = st.text_input("Notes (optional)", key="c_notes")

        if st.button("Log Meal", type="primary"):
            if meal_name.strip():
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO calorie_logs (user_id, date, meal_name, calories, notes) VALUES (?, ?, ?, ?, ?)",
                        (uid, str(c_date), meal_name.strip(), calories, c_notes),
                    )
                    conn.commit()
                st.session_state.pop("_cal_est_label", None)
                st.success(f"Logged {meal_name}: {calories} cal")
                st.session_state["_goto_tab"] = 1
                st.rerun()
            else:
                st.warning("Please enter a meal name.")

    with col_chart:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, date, meal_name, calories, notes FROM calorie_logs WHERE user_id = ? ORDER BY date DESC, id DESC",
                (uid,),
            ).fetchall()

        if rows:
            df = pd.DataFrame(rows, columns=["ID", "Date", "Meal", "Calories", "Notes"])
            df["Date"] = pd.to_datetime(df["Date"])

            daily = df.groupby("Date")["Calories"].sum().reset_index()
            st.bar_chart(daily.set_index("Date"))

            # ── Inline table with Edit / Delete per row ───────────────────────
            h = st.columns([2, 5, 1, 1, 1])
            for col, label in zip(h, ["Date", "Meal", "Cal", "", ""]):
                col.markdown(f"**{label}**")
            st.divider()

            for _, row in df.iterrows():
                row_id = int(row["ID"])
                c = st.columns([2, 5, 1, 1, 1])
                c[0].write(row["Date"].strftime("%m/%d"))
                c[1].write(row["Meal"])
                c[2].write(str(row["Calories"]))
                if c[3].button("✏️", key=f"ec_open_{row_id}", help="Edit"):
                    st.session_state["editing_cal_id"] = row_id
                if c[4].button("✕", key=f"ec_del_{row_id}", help="Delete"):
                    with get_conn() as conn:
                        conn.execute(
                            "DELETE FROM calorie_logs WHERE id = ? AND user_id = ?",
                            (row_id, uid),
                        )
                        conn.commit()
                    st.session_state.pop("editing_cal_id", None)
                    st.session_state["_goto_tab"] = 1
                    st.rerun()

            # ── Edit form ─────────────────────────────────────────────────────
            editing_cid = st.session_state.get("editing_cal_id")
            if editing_cid and editing_cid in df["ID"].values:
                edit_row = df[df["ID"] == editing_cid].iloc[0]
                st.divider()
                st.markdown(f"**Editing entry — {edit_row['Date'].strftime('%Y-%m-%d')} / {edit_row['Meal']}**")
                with st.form("edit_cal_form"):
                    e_date     = st.date_input("Date", value=edit_row["Date"].date())
                    e_meal     = st.text_input("Food / Meal", value=edit_row["Meal"])
                    e_calories = st.number_input("Calories", min_value=0, max_value=5000, step=5,
                                                 value=int(edit_row["Calories"]))
                    e_notes    = st.text_input("Notes", value=edit_row["Notes"] or "")
                    col_save, col_cancel = st.columns(2)
                    saved     = col_save.form_submit_button("Save", type="primary")
                    cancelled = col_cancel.form_submit_button("Cancel")

                if saved:
                    if e_meal.strip():
                        with get_conn() as conn:
                            conn.execute(
                                "UPDATE calorie_logs SET date=?, meal_name=?, calories=?, notes=? WHERE id=? AND user_id=?",
                                (str(e_date), e_meal.strip(), e_calories, e_notes, editing_cid, uid),
                            )
                            conn.commit()
                        st.session_state.pop("editing_cal_id", None)
                        st.session_state["_goto_tab"] = 1
                        st.rerun()
                    else:
                        st.warning("Meal name cannot be empty.")
                if cancelled:
                    st.session_state.pop("editing_cal_id", None)
                    st.session_state["_goto_tab"] = 1
                    st.rerun()
        else:
            st.info("No meals logged yet. Track your first meal on the left!")


# ── Tab 4: Meal Planner ───────────────────────────────────────────────────────
with tab_planner:
    from itertools import groupby as _groupby

    all_planned = get_planned_meals(uid)
    today_str   = str(date.today())
    today_meals  = [m for m in all_planned if m["planned_date"] == today_str]
    future_meals = [m for m in all_planned if m["planned_date"] > today_str]

    # ── Today's meals ─────────────────────────────────────────────────────────
    if today_meals:
        total_plan_cal = sum(m["calories"] for m in today_meals)
        done_plan_cal  = sum(m["calories"] for m in today_meals if m["done"])
        done_plan_cnt  = sum(1 for m in today_meals if m["done"])
        st.markdown(
            f"### Today &nbsp; · &nbsp; {done_plan_cnt}/{len(today_meals)} meals "
            f"&nbsp; · &nbsp; {done_plan_cal:,} / {total_plan_cal:,} cal"
        )
        st.progress(min(done_plan_cal / total_plan_cal, 1.0) if total_plan_cal else 0.0)

        for _m in today_meals:
            _mid    = _m["id"]
            _done   = bool(_m["done"])
            tc1, tc2, tc3 = st.columns([0.07, 0.73, 0.20])
            with tc1:
                _checked = st.checkbox("", value=_done, key=f"tp_{_mid}", label_visibility="collapsed")
                if _checked != _done:
                    mark_planned_meal_done(_mid, uid, _checked)
                    if _checked:
                        with get_conn() as _pc:
                            _pc.execute(
                                "INSERT INTO calorie_logs (user_id, date, meal_name, calories, notes)"
                                " VALUES (?, ?, ?, ?, ?)",
                                (uid, today_str, _m["meal_name"], _m["calories"], "from plan"),
                            )
                            _pc.commit()
                    st.session_state["_goto_tab"] = 3
                    st.rerun()
            with tc2:
                if _done:
                    st.markdown(
                        f"<span style='color:grey;text-decoration:line-through'>"
                        f"{_m['meal_type']} — {_m['meal_name']}</span>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"**{_m['meal_type']}** — {_m['meal_name']}")
            with tc3:
                st.write(f"{_m['calories']:,} cal")

        st.divider()
    elif all_planned:
        st.info("All meals for today are done. Generate a new plan below when ready.")
    else:
        st.info("No plan yet — generate one below and hit **Start today**.")

    # ── Upcoming days (collapsed) ─────────────────────────────────────────────
    if future_meals:
        _future_days = sorted(set(m["planned_date"] for m in future_meals))
        with st.expander(f"Upcoming — {len(_future_days)} day(s)"):
            for _fday, _frows in _groupby(future_meals, key=lambda r: r["planned_date"]):
                _frows = list(_frows)
                _fday_cal = sum(r["calories"] for r in _frows)
                st.markdown(f"**{_fday}** — {_fday_cal:,} cal")
                for _fr in _frows:
                    _fc1, _fc2 = st.columns([4, 1])
                    _fc1.write(f"{_fr['meal_type']} — {_fr['meal_name']}")
                    _fc2.write(f"{_fr['calories']:,} cal")
        st.divider()

    # ── Generate new plan ─────────────────────────────────────────────────────
    st.markdown("### Generate a New Plan")

    with get_conn() as _plw:
        _pl_lw = _plw.execute(
            "SELECT weight_lbs FROM weight_logs WHERE user_id=? ORDER BY date DESC LIMIT 1", (uid,)
        ).fetchone()
    _default_cals = ((_calc_tdee(profile, _pl_lw["weight_lbs"]) or 2300) - 500) if _pl_lw else 1800
    _default_cals = max(800, min(5000, _default_cals))

    pl1, pl2 = st.columns(2)
    with pl1:
        period         = st.radio("Plan for", ["Daily", "Weekly"], horizontal=True, key="pl_period")
        calorie_target = st.number_input("Daily calorie target", min_value=800, max_value=5000,
                                         value=_default_cals, step=50, key="pl_cals")
    with pl2:
        dietary_prefs = st.text_input("Dietary preferences",
                                      value=profile["dietary_prefs"] or "" if profile else "",
                                      placeholder="e.g. vegetarian, low-carb", key="pl_prefs")
        allergies     = st.text_input("Allergies / avoid",
                                      value=profile["allergies"] or "" if profile else "",
                                      placeholder="e.g. nuts, dairy", key="pl_allergies")

    ingredients = st.text_area("Ingredients you have (optional)",
                                placeholder="e.g. chicken, broccoli, eggs…", height=60, key="pl_ing")

    if st.button("Generate Meal Plan", type="primary", use_container_width=True):
        if _check_rate_limit(uid):
            with st.spinner("Building your meal plan…"):
                _new_plan = generate_meal_plan(
                    calorie_target, period, dietary_prefs, allergies, ingredients, profile=profile
                )
            increment_usage(uid)
            st.session_state["new_plan_text"]   = _new_plan
            st.session_state["new_plan_period"] = period

    if "new_plan_text" in st.session_state:
        _np_text   = st.session_state["new_plan_text"]
        _np_parsed = _parse_meal_plan(_np_text)

        if _np_parsed:
            _np_days = sorted(set(m["day"] for m in _np_parsed))
            for _nd in _np_days:
                _nd_meals = [m for m in _np_parsed if m["day"] == _nd]
                _nd_total = sum(m["calories"] for m in _nd_meals)
                if len(_np_days) > 1:
                    st.markdown(f"**Day {_nd}** — {_nd_total:,} cal")
                _nph = st.columns([2, 5, 2])
                _nph[0].markdown("**Meal**"); _nph[1].markdown("**Food**"); _nph[2].markdown("**Cal**")
                for _nm in _nd_meals:
                    _npc = st.columns([2, 5, 2])
                    _npc[0].write(_nm["type"]); _npc[1].write(_nm["name"]); _npc[2].write(f"{_nm['calories']:,}")
                if len(_np_days) > 1:
                    st.divider()
        else:
            st.markdown(_np_text)

        _na1, _na2 = st.columns(2)
        with _na1:
            if st.button("Start today", type="primary", use_container_width=True):
                if _np_parsed:
                    _saved_pid = save_meal_plan(uid, st.session_state["new_plan_period"], _np_text)
                    _meals_dated = [
                        {**m, "planned_date": str(date.today() + pd.Timedelta(days=m["day"] - 1))}
                        for m in _np_parsed
                    ]
                    activate_meal_plan(uid, _saved_pid, _meals_dated)
                    st.session_state.pop("new_plan_text", None)
                    st.session_state.pop("new_plan_period", None)
                    st.success("Plan activated! Check off meals above as you eat them.")
                    st.session_state["_goto_tab"] = 3
                    st.rerun()
                else:
                    st.warning("Could not parse this plan — try regenerating.")
        with _na2:
            st.download_button("Download", data=_np_text, file_name="meal_plan.txt",
                               mime="text/plain", use_container_width=True)

    # ── Clear plan ────────────────────────────────────────────────────────────
    if all_planned:
        st.divider()
        if st.button("Clear all scheduled meals", type="secondary"):
            with get_conn() as _clr:
                _clr.execute("DELETE FROM planned_meals WHERE user_id=?", (uid,))
                _clr.commit()
            st.session_state["_goto_tab"] = 3
            st.rerun()


# ── Tab 5: Calorie Lookup ─────────────────────────────────────────────────────
with tab_scanner:
    st.subheader("Calorie Lookup")
    st.caption("Describe what you ate or scan a photo — AI estimates calories and nutrition.")

    col_input, col_result = st.columns([1, 1])

    with col_input:
        input_mode = st.radio(
            "Input method",
            ["Describe food", "Upload image", "Take photo"],
            horizontal=True,
        )

        ready = False

        if input_mode == "Describe food":
            food_desc = st.text_area(
                "What did you eat?",
                placeholder="e.g. 2 scrambled eggs with toast and a glass of orange juice",
                height=120,
            )
            ready = bool(food_desc.strip())
            if st.button("Analyse", type="primary", disabled=not ready):
                if _check_rate_limit(uid):
                    with st.spinner("Estimating…"):
                        result = analyze_food_text(food_desc.strip(), profile=profile)
                    increment_usage(uid)
                    _extract_scan_result(result)

        else:
            image_file = (
                st.file_uploader("Choose a food photo", type=["jpg", "jpeg", "png", "webp"])
                if input_mode == "Upload image"
                else st.camera_input("Take a photo of your food")
            )
            if image_file:
                st.image(image_file, use_container_width=True)
                if st.button("Analyse", type="primary"):
                    name = getattr(image_file, "name", "photo.jpg").lower()
                    media_type = (
                        "image/png" if name.endswith(".png")
                        else "image/webp" if name.endswith(".webp")
                        else "image/jpeg"
                    )
                    if _check_rate_limit(uid):
                        with st.spinner("Analysing your food…"):
                            result = analyze_food_image(image_file.getvalue(), media_type, profile=profile)
                        increment_usage(uid)
                        _extract_scan_result(result)

    with col_result:
        if "scan_result" in st.session_state:
            st.markdown(st.session_state["scan_result"])
            st.divider()
            st.markdown("**Log this to your Calorie Tracker**")
            log_name = st.text_input("Meal label", value="Looked up meal", key="scan_name")
            log_cals = st.number_input(
                "Calories", min_value=0, max_value=5000, step=5,
                value=st.session_state.get("scan_calories", 0),
                key="scan_log_cals",
            )
            if st.button("Log to Calorie Tracker"):
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO calorie_logs (user_id, date, meal_name, calories, notes) VALUES (?, ?, ?, ?, ?)",
                        (uid, str(date.today()), log_name, log_cals, "via Calorie Lookup"),
                    )
                    conn.commit()
                st.success(f"Logged {log_name}: {log_cals} cal")
        else:
            st.info("Your nutrition analysis will appear here.")
