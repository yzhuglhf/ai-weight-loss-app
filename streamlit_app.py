import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import streamlit as st
import pandas as pd
from datetime import date

from app.db import init_db, get_conn
from app.services.ai import chat, generate_meal_plan, analyze_food_image

init_db()

st.set_page_config(page_title="AI Weight Loss Coach", layout="wide")
st.title("AI Weight Loss Coach")

tab_ai, tab_weight, tab_calories, tab_planner, tab_scanner = st.tabs(
    ["AI Coach", "Weight Tracker", "Calorie Log", "Meal Planner", "Food Scanner"]
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
            with st.spinner("Thinking…"):
                reply = chat(st.session_state.messages)
            st.markdown(reply)

        st.session_state.messages.append({"role": "assistant", "content": reply})


# ── Tab 2: Weight Tracker ─────────────────────────────────────────────────────
with tab_weight:
    st.subheader("Track Your Weight")

    col_form, col_chart = st.columns([1, 2])

    with col_form:
        w_date = st.date_input("Date", value=date.today(), key="w_date")
        weight = st.number_input("Weight (lbs)", min_value=50.0, max_value=600.0, step=0.1, key="w_val")
        w_notes = st.text_input("Notes (optional)", key="w_notes")

        if st.button("Log Weight", type="primary"):
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO weight_logs (date, weight_lbs, notes) VALUES (?, ?, ?)",
                    (str(w_date), weight, w_notes),
                )
                conn.commit()
            st.success(f"Logged {weight} lbs on {w_date}")
            st.rerun()

    with col_chart:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT date, weight_lbs, notes FROM weight_logs ORDER BY date ASC"
            ).fetchall()

        if rows:
            df = pd.DataFrame(rows, columns=["Date", "Weight (lbs)", "Notes"])
            df["Date"] = pd.to_datetime(df["Date"])

            start_w = df["Weight (lbs)"].iloc[0]
            latest_w = df["Weight (lbs)"].iloc[-1]
            delta = latest_w - start_w
            delta_str = f"{delta:+.1f} lbs since start"

            c1, c2 = st.columns(2)
            c1.metric("Current Weight", f"{latest_w:.1f} lbs", delta_str)
            c2.metric("Entries", len(df))

            st.line_chart(df.set_index("Date")["Weight (lbs)"])
            st.dataframe(df.sort_values("Date", ascending=False).reset_index(drop=True), hide_index=True)
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
                        "INSERT INTO calorie_logs (date, meal_name, calories, notes) VALUES (?, ?, ?, ?)",
                        (str(c_date), meal_name.strip(), calories, c_notes),
                    )
                    conn.commit()
                st.success(f"Logged {meal_name}: {calories} cal")
                st.rerun()
            else:
                st.warning("Please enter a meal name.")

    with col_chart:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT date, meal_name, calories, notes FROM calorie_logs ORDER BY date DESC, id DESC"
            ).fetchall()

        if rows:
            df = pd.DataFrame(rows, columns=["Date", "Meal", "Calories", "Notes"])
            df["Date"] = pd.to_datetime(df["Date"])

            today_total = df[df["Date"].dt.date == date.today()]["Calories"].sum()
            st.metric("Today's Calories", f"{today_total} cal")

            daily = df.groupby("Date")["Calories"].sum().reset_index()
            st.bar_chart(daily.set_index("Date"))

            st.dataframe(df.reset_index(drop=True), hide_index=True)
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
        dietary_prefs = st.text_input(
            "Dietary preferences", placeholder="e.g. vegetarian, low-carb, Mediterranean"
        )
        allergies = st.text_input(
            "Allergies / foods to avoid", placeholder="e.g. nuts, dairy, gluten"
        )

        generate = st.button("Generate Meal Plan", type="primary")

    with col_plan:
        if generate:
            with st.spinner("Building your meal plan…"):
                plan = generate_meal_plan(calorie_target, period, dietary_prefs, allergies)
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


# ── Tab 5: Food Scanner ───────────────────────────────────────────────────────
with tab_scanner:
    st.subheader("Food Scanner")
    st.caption("Take a photo or upload an image — AI will estimate calories and nutrition.")

    col_input, col_result = st.columns([1, 1])

    with col_input:
        input_mode = st.radio("Input method", ["Upload image", "Take photo"], horizontal=True)

        image_file = None
        if input_mode == "Upload image":
            image_file = st.file_uploader(
                "Choose a food photo", type=["jpg", "jpeg", "png", "webp"]
            )
        else:
            image_file = st.camera_input("Take a photo of your food")

        if image_file:
            st.image(image_file, use_container_width=True)

            if st.button("Analyse", type="primary"):
                # Determine MIME type
                name = getattr(image_file, "name", "photo.jpg").lower()
                if name.endswith(".png"):
                    media_type = "image/png"
                elif name.endswith(".webp"):
                    media_type = "image/webp"
                else:
                    media_type = "image/jpeg"

                with st.spinner("Analysing your food…"):
                    result = analyze_food_image(image_file.getvalue(), media_type)

                st.session_state["scan_result"] = result

                # Extract calorie number for the quick-log button
                cal_hint = 0
                for line in result.splitlines():
                    if "estimated calories" in line.lower():
                        import re
                        nums = re.findall(r"\d+", line)
                        if nums:
                            cal_hint = int(nums[0])
                        break
                st.session_state["scan_calories"] = cal_hint

    with col_result:
        if "scan_result" in st.session_state:
            st.markdown(st.session_state["scan_result"])

            st.divider()
            st.markdown("**Log this to your Calorie Tracker**")
            log_name = st.text_input("Meal label", value="Scanned meal", key="scan_name")
            log_cals = st.number_input(
                "Calories", min_value=0, max_value=5000, step=5,
                value=st.session_state.get("scan_calories", 0),
                key="scan_log_cals",
            )
            if st.button("Log to Calorie Tracker"):
                with get_conn() as conn:
                    conn.execute(
                        "INSERT INTO calorie_logs (date, meal_name, calories, notes) VALUES (?, ?, ?, ?)",
                        (str(date.today()), log_name, log_cals, "via Food Scanner"),
                    )
                    conn.commit()
                st.success(f"Logged {log_name}: {log_cals} cal")
        else:
            st.info("Your nutrition analysis will appear here.")
