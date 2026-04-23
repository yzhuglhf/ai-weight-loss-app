import hmac
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import extra_streamlit_components as stx
import streamlit as st
import pandas as pd
from datetime import date
from dotenv import load_dotenv

load_dotenv()

from app.db import (
    init_db, get_conn,
    authenticate, create_user, get_user_by_email,
    create_session, get_session_user, delete_session,
    get_usage_today, increment_usage,
)
from app.services.ai import chat, generate_meal_plan, analyze_food_image, analyze_food_text

init_db()

st.set_page_config(page_title="AI Weight Loss Coach", layout="wide")

# Hide the CookieManager iframe (extra-streamlit-components renders a blank component)
# and tighten the default top padding.
st.markdown(
    """
    <style>
    iframe[title="extra_streamlit_components.CookieManager"] {
        display: none !important;
        height: 0 !important;
    }
    .block-container { padding-top: 1.5rem !important; }
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
    st.title("AI Weight Loss Coach")

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

st.title("AI Weight Loss Coach")

tab_ai, tab_weight, tab_calories, tab_planner, tab_scanner = st.tabs(
    ["AI Coach", "Weight Tracker", "Calorie Log", "Meal Planner", "Calorie Lookup"]
)


# ── Tab 1: AI Chat ────────────────────────────────────────────────────────────
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
                    reply = chat(st.session_state.messages)
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
                    st.rerun()
                if cancelled:
                    st.session_state.pop("editing_weight_id", None)
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
                        st.rerun()
                    else:
                        st.warning("Meal name cannot be empty.")
                if cancelled:
                    st.session_state.pop("editing_cal_id", None)
                    st.rerun()
        else:
            st.info("No meals logged yet. Track your first meal on the left!")


# ── Tab 4: Meal Planner ───────────────────────────────────────────────────────
with tab_planner:
    st.subheader("AI Meal Planner")
    st.caption("Generate a personalised meal plan based on your calorie target.")

    col_opts, col_plan = st.columns([1, 2])

    with col_opts:
        period = st.radio("Plan for", ["Daily", "Weekly"], horizontal=True)
        calorie_target = st.number_input(
            "Daily calorie target", min_value=800, max_value=5000, value=1800, step=50
        )
        ingredients = st.text_area(
            "Ingredients you have",
            placeholder="e.g. chicken breast, broccoli, eggs, brown rice, olive oil, garlic…\n\nLeave blank for a general plan.",
            height=120,
        )
        dietary_prefs = st.text_input(
            "Dietary preferences", placeholder="e.g. vegetarian, low-carb, Mediterranean"
        )
        allergies = st.text_input(
            "Allergies / foods to avoid", placeholder="e.g. nuts, dairy, gluten"
        )

        generate = st.button("Generate Meal Plan", type="primary")

    with col_plan:
        if generate:
            if _check_rate_limit(uid):
                with st.spinner("Building your meal plan…"):
                    plan = generate_meal_plan(calorie_target, period, dietary_prefs, allergies, ingredients)
                increment_usage(uid)
                st.session_state["meal_plan"] = plan

        if "meal_plan" in st.session_state:
            st.markdown(st.session_state["meal_plan"])
            st.download_button(
                "Download plan as text",
                data=st.session_state["meal_plan"],
                file_name="meal_plan.txt",
                mime="text/plain",
            )
        else:
            st.info("Set your preferences on the left and click **Generate Meal Plan**.")


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
                        result = analyze_food_text(food_desc.strip())
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
                            result = analyze_food_image(image_file.getvalue(), media_type)
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
