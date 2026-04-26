import hmac
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import extra_streamlit_components as stx
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from app.db import (
    init_db, get_conn,
    authenticate, create_user, get_user_by_email,
    create_session, get_session_user, delete_session,
    get_usage_today, increment_usage,
    get_profile, save_profile,
    save_meal_plan, get_meal_plans, update_meal_plan, delete_meal_plan,
    activate_meal_plan, get_planned_meals, mark_planned_meal_done, update_planned_meal, delete_planned_meal,
)
from app.services.ai import chat, generate_meal_plan, analyze_food_image, analyze_food_text

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

COOKIE_NAME = "wl_session"
INVITE_CODE = os.getenv("INVITE_CODE", "")
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


cookie_manager = stx.CookieManager()


# ── Restore session from cookie on refresh ────────────────────────────────────
if not st.session_state.get("user_id"):
    token = cookie_manager.get(COOKIE_NAME)
    if token:
        user = get_session_user(token)
        if user:
            st.session_state["user_id"] = user["id"]
            st.session_state["user_email"] = user["email"]
            st.session_state["session_token"] = token


# ── Auth gate ─────────────────────────────────────────────────────────────────
if not st.session_state.get("user_id"):
    st.title("NutriCoach")

    tab_login, tab_signup = st.tabs(["Log in", "Sign up"])

    with tab_login:
        with st.form("login_form"):
            li_email = st.text_input("Email")
            li_password = st.text_input("Password", type="password")
            li_submit = st.form_submit_button("Log in", type="primary")
        if li_submit:
            user = authenticate(li_email, li_password)
            if user:
                token = create_session(user["id"])
                cookie_manager.set(COOKIE_NAME, token)
                st.session_state["user_id"] = user["id"]
                st.session_state["user_email"] = user["email"]
                st.session_state["session_token"] = token
                st.rerun()
            else:
                st.error("Invalid email or password.")

    with tab_signup:
        with st.form("signup_form"):
            su_email = st.text_input("Email", key="su_email")
            su_password = st.text_input("Password", type="password", key="su_pw")
            su_confirm = st.text_input("Confirm password", type="password", key="su_confirm")
            if INVITE_CODE:
                su_invite = st.text_input("Invite code", key="su_invite")
            su_submit = st.form_submit_button("Create account", type="primary")
        if su_submit:
            invite_ok = (not INVITE_CODE) or hmac.compare_digest(
                su_invite.strip(), INVITE_CODE
            )
            if not su_email or not su_password:
                st.error("Email and password are required.")
            elif not invite_ok:
                st.error("Invalid invite code.")
            elif su_password != su_confirm:
                st.error("Passwords don't match.")
            elif len(su_password) < 8:
                st.error("Password must be at least 8 characters.")
            elif get_user_by_email(su_email):
                st.error("An account with that email already exists.")
            else:
                create_user(su_email, su_password)
                st.success("Account created! Switch to the Log in tab.")

    st.stop()


# ── Sidebar ───────────────────────────────────────────────────────────────────
uid = st.session_state["user_id"]

with st.sidebar:
    st.markdown(f"**{st.session_state['user_email']}**")
    used_today = get_usage_today(uid)
    st.caption(f"AI requests today: {used_today} / {DAILY_AI_LIMIT}")
    st.progress(min(used_today / DAILY_AI_LIMIT, 1.0))
    if st.button("Log out"):
        token = st.session_state.get("session_token")
        if token:
            delete_session(token)
            cookie_manager.delete(COOKIE_NAME)
        for key in ["user_id", "user_email", "session_token", "messages", "meal_plan", "scan_result", "scan_calories"]:
            st.session_state.pop(key, None)
        st.rerun()

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

# Load profile once per render — used by all tabs
profile = get_profile(uid)

tab_profile, tab_ai, tab_weight, tab_calories, tab_planner, tab_scanner = st.tabs(
    ["Profile", "AI Coach", "Weight", "Calories", "Meal Plan", "Scanner"]
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
            st.session_state["_goto_tab"] = 0
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


# ── Tab 2: AI Chat ────────────────────────────────────────────────────────────
with tab_ai:
    st.subheader("Chat with your AI Coach")
    st.caption("Ask about meals, workouts, calorie estimates, or weight loss tips.")

    if "messages" not in st.session_state:
        st.session_state.messages = []

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("e.g. Suggest a low-carb dinner under 500 calories…"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            if _check_rate_limit(uid):
                with st.spinner("Thinking…"):
                    reply = chat(st.session_state.messages, profile=profile)
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
            h = st.columns([2, 2, 3, 1, 1])
            for col, label in zip(h, ["Date", f"Weight ({w_unit})", "Notes", "Edit", "Delete"]):
                col.markdown(f"**{label}**")
            st.divider()

            for _, row in sorted_df.iterrows():
                row_id = int(row["ID"])
                c = st.columns([2, 2, 3, 1, 1])
                c[0].write(row["Date"].strftime("%Y-%m-%d"))
                c[1].write(f"{row[display_col]:.1f}")
                c[2].write(row["Notes"] or "—")
                if c[3].button("Edit", key=f"ew_open_{row_id}"):
                    st.session_state["editing_weight_id"] = row_id
                if c[4].button("Delete", key=f"ew_del_{row_id}"):
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


# ── Tab 3: Calorie Log ────────────────────────────────────────────────────────
with tab_calories:
    st.subheader("Track Your Calories")

    col_form, col_chart = st.columns([1, 2])

    with col_form:
        c_date = st.date_input("Date", value=date.today(), key="c_date")
        meal_name = st.text_input("Food / Meal", key="c_meal")
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
                st.success(f"Logged {meal_name}: {calories} cal")
                st.session_state["_goto_tab"] = 3
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

            today_total = df[df["Date"].dt.date == date.today()]["Calories"].sum()
            st.metric("Today's Calories", f"{today_total} cal")

            daily = df.groupby("Date")["Calories"].sum().reset_index()
            st.bar_chart(daily.set_index("Date"))

            # ── Inline table with Edit / Delete per row ───────────────────────
            h = st.columns([2, 3, 1, 2, 1, 1])
            for col, label in zip(h, ["Date", "Meal", "Cal", "Notes", "Edit", "Delete"]):
                col.markdown(f"**{label}**")
            st.divider()

            for _, row in df.iterrows():
                row_id = int(row["ID"])
                c = st.columns([2, 3, 1, 2, 1, 1])
                c[0].write(row["Date"].strftime("%Y-%m-%d"))
                c[1].write(row["Meal"])
                c[2].write(str(row["Calories"]))
                c[3].write(row["Notes"] or "—")
                if c[4].button("Edit", key=f"ec_open_{row_id}"):
                    st.session_state["editing_cal_id"] = row_id
                if c[5].button("Delete", key=f"ec_del_{row_id}"):
                    with get_conn() as conn:
                        conn.execute(
                            "DELETE FROM calorie_logs WHERE id = ? AND user_id = ?",
                            (row_id, uid),
                        )
                        conn.commit()
                    st.session_state.pop("editing_cal_id", None)
                    st.session_state["_goto_tab"] = 3
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
                        st.session_state["_goto_tab"] = 3
                        st.rerun()
                    else:
                        st.warning("Meal name cannot be empty.")
                if cancelled:
                    st.session_state.pop("editing_cal_id", None)
                    st.session_state["_goto_tab"] = 3
                    st.rerun()
        else:
            st.info("No meals logged yet. Track your first meal on the left!")


# ── Tab 4: Meal Planner ───────────────────────────────────────────────────────
with tab_planner:
    st.subheader("AI Meal Planner")
    st.caption("Generate a personalised meal plan based on your calorie target.")

    col_opts, col_plan = st.columns([1, 2])

    with col_opts:
        # Default calorie target: TDEE - 500 from profile, else 1800
        with get_conn() as _conn:
            _lw = _conn.execute(
                "SELECT weight_lbs FROM weight_logs WHERE user_id = ? ORDER BY date DESC LIMIT 1", (uid,)
            ).fetchone()
        _default_cals = (_calc_tdee(profile, _lw["weight_lbs"]) or 2300) - 500 if _lw else 1800
        _default_cals = max(800, min(5000, _default_cals))

        period = st.radio("Plan for", ["Daily", "Weekly"], horizontal=True)
        calorie_target = st.number_input(
            "Daily calorie target", min_value=800, max_value=5000, value=_default_cals, step=50
        )
        ingredients = st.text_area(
            "Ingredients you have",
            placeholder="e.g. chicken breast, broccoli, eggs, brown rice…  (leave blank for a general plan)",
            height=80,
        )
        dietary_prefs = st.text_input(
            "Dietary preferences",
            value=profile["dietary_prefs"] or "" if profile else "",
            placeholder="e.g. vegetarian, low-carb, Mediterranean",
        )
        allergies = st.text_input(
            "Allergies / foods to avoid",
            value=profile["allergies"] or "" if profile else "",
            placeholder="e.g. nuts, dairy, gluten",
        )

        generate = st.button("Generate Meal Plan", type="primary")

    with col_plan:
        if generate:
            if _check_rate_limit(uid):
                with st.spinner("Building your meal plan…"):
                    plan = generate_meal_plan(calorie_target, period, dietary_prefs, allergies, ingredients, profile=profile)
                increment_usage(uid)
                st.session_state["meal_plan"] = plan
                st.session_state["meal_plan_period"] = period

        if "meal_plan" in st.session_state:
            st.markdown(st.session_state["meal_plan"])
            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                st.download_button(
                    "Download as text",
                    data=st.session_state["meal_plan"],
                    file_name="meal_plan.txt",
                    mime="text/plain",
                )
            with btn_col2:
                if st.button("Save Plan", type="primary"):
                    save_meal_plan(uid, st.session_state.get("meal_plan_period", "Daily"),
                                   st.session_state["meal_plan"])
                    st.success("Plan saved to My Plans!")
        else:
            st.info("Set your preferences on the left and click **Generate Meal Plan**.")

    # ── My Saved Plans ────────────────────────────────────────────────────────
    st.divider()
    st.subheader("My Plans")

    saved_plans = get_meal_plans(uid)
    if not saved_plans:
        st.info("No saved plans yet. Generate a plan above and click **Save Plan**.")
    else:
        for plan_row in saved_plans:
            pid         = plan_row["id"]
            created     = plan_row["created_at"][:10]
            plan_period = plan_row["period"]
            label = f"{created}  —  {plan_period} plan"

            with st.expander(label):
                editing_plan = st.session_state.get("editing_plan_id") == pid

                if editing_plan:
                    new_text = st.text_area(
                        "Edit plan text",
                        value=plan_row["plan_text"],
                        height=300,
                        key=f"edit_plan_text_{pid}",
                    )
                    ep_col1, ep_col2 = st.columns(2)
                    if ep_col1.button("Save changes", type="primary", key=f"ep_save_{pid}"):
                        update_meal_plan(pid, uid, new_text.strip())
                        st.session_state.pop("editing_plan_id", None)
                        st.session_state["_goto_tab"] = 4
                        st.rerun()
                    if ep_col2.button("Cancel", key=f"ep_cancel_{pid}"):
                        st.session_state.pop("editing_plan_id", None)
                        st.session_state["_goto_tab"] = 4
                        st.rerun()
                else:
                    st.markdown(plan_row["plan_text"])

                    st.divider()
                    act_col1, act_col2, act_col3 = st.columns([1, 1, 1])

                    with act_col1:
                        if act_col1.button("✏️ Edit plan", key=f"ep_open_{pid}"):
                            st.session_state["editing_plan_id"] = pid
                            st.session_state["_goto_tab"] = 4
                            st.rerun()

                    parsed = _parse_meal_plan(plan_row["plan_text"])
                    if parsed:
                        with act_col2:
                            start_date = st.date_input(
                                "Start date", value=date.today(), key=f"plan_date_{pid}"
                            )
                        with act_col3:
                            st.write("")
                            if st.button(f"Add {len(parsed)} meals →", type="primary", key=f"add_plan_{pid}"):
                                meals_with_dates = [
                                    {**m, "planned_date": str(start_date + pd.Timedelta(days=m["day"] - 1))}
                                    for m in parsed
                                ]
                                activate_meal_plan(uid, pid, meals_with_dates)
                                st.success(f"Added {len(parsed)} meals to your Planner!")
                    else:
                        with act_col2:
                            st.warning("Could not parse meals — try editing the plan text.")

                    st.divider()
                    if st.button("Delete this plan", type="secondary", key=f"del_plan_{pid}"):
                        delete_meal_plan(pid, uid)
                        st.session_state["_goto_tab"] = 4
                        st.rerun()

    # ── My Planner ────────────────────────────────────────────────────────────
    st.divider()
    st.subheader("My Planner")
    st.caption("Meals you've scheduled — check them off as you go.")

    planned = get_planned_meals(uid)
    if not planned:
        st.info("No meals planned yet. Use **Add to Planner** above to schedule a meal plan.")
    else:
        # Group by planned_date
        from itertools import groupby
        keyfn = lambda r: r["planned_date"]
        for day_date, day_rows in groupby(planned, key=keyfn):
            day_rows = list(day_rows)
            done_count = sum(1 for r in day_rows if r["done"])
            total_count = len(day_rows)
            total_cal = sum(r["calories"] for r in day_rows)
            done_cal  = sum(r["calories"] for r in day_rows if r["done"])

            all_done = done_count == total_count
            day_icon = "✅" if all_done else "🗓️"
            day_label = f"{day_icon} {day_date}  ·  {done_count}/{total_count}  ·  {done_cal}/{total_cal} cal"
            with st.expander(day_label, expanded=(day_date == str(date.today()))):
                for row in day_rows:
                    meal_id   = row["id"]
                    is_done   = bool(row["done"])
                    meal_text = f"**{row['meal_type']}** — {row['meal_name']} · {row['calories']} cal"
                    editing_this = st.session_state.get("editing_planned_id") == meal_id

                    c1, c2, c3, c4 = st.columns([0.04, 0.72, 0.12, 0.12])
                    with c1:
                        checked = st.checkbox(
                            "", value=is_done, key=f"done_{meal_id}",
                            label_visibility="collapsed"
                        )
                        if checked != is_done:
                            mark_planned_meal_done(meal_id, uid, checked)
                            st.session_state["_goto_tab"] = 4
                            st.rerun()
                    with c2:
                        if is_done:
                            st.markdown(f"<span style='color:grey;text-decoration:line-through'>{row['meal_type']} — {row['meal_name']} · {row['calories']} cal</span>", unsafe_allow_html=True)
                        else:
                            st.markdown(meal_text)
                    with c3:
                        if st.button("✏️", key=f"edit_meal_{meal_id}", help="Edit"):
                            if editing_this:
                                st.session_state.pop("editing_planned_id", None)
                            else:
                                st.session_state["editing_planned_id"] = meal_id
                            st.session_state["_goto_tab"] = 4
                            st.rerun()
                    with c4:
                        if st.button("✕", key=f"rm_meal_{meal_id}", help="Remove"):
                            delete_planned_meal(meal_id, uid)
                            st.session_state.pop("editing_planned_id", None)
                            st.session_state["_goto_tab"] = 4
                            st.rerun()

                    if editing_this:
                        with st.form(key=f"edit_meal_form_{meal_id}"):
                            ef_col1, ef_col2, ef_col3 = st.columns([1, 2, 1])
                            e_type = ef_col1.selectbox(
                                "Type",
                                ["Breakfast", "Lunch", "Dinner", "Snacks", "Other"],
                                index=["Breakfast","Lunch","Dinner","Snacks","Other"].index(row["meal_type"])
                                      if row["meal_type"] in ["Breakfast","Lunch","Dinner","Snacks","Other"] else 4,
                                key=f"ef_type_{meal_id}",
                            )
                            e_name = ef_col2.text_input("Meal", value=row["meal_name"], key=f"ef_name_{meal_id}")
                            e_cal  = ef_col3.number_input("Cal", min_value=0, max_value=5000, step=5,
                                                          value=int(row["calories"]), key=f"ef_cal_{meal_id}")
                            fs1, fs2 = st.columns(2)
                            if fs1.form_submit_button("Save", type="primary"):
                                if e_name.strip():
                                    update_planned_meal(meal_id, uid, e_type, e_name.strip(), e_cal)
                                    st.session_state.pop("editing_planned_id", None)
                                    st.session_state["_goto_tab"] = 4
                                    st.rerun()
                            if fs2.form_submit_button("Cancel"):
                                st.session_state.pop("editing_planned_id", None)
                                st.session_state["_goto_tab"] = 4
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
