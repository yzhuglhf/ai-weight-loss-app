import base64
import os

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

# On Streamlit Cloud, secrets are injected as env vars via the Secrets manager.
# Fall back to st.secrets if the env var isn't present (e.g. during local dev
# where a secrets.toml is used instead of a .env file).
if not os.environ.get("ANTHROPIC_API_KEY"):
    try:
        import streamlit as st
        os.environ["ANTHROPIC_API_KEY"] = st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        pass

client = Anthropic()

CHAT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are NutriCoach, a supportive AI weight loss coach. You help users with:
- Food suggestions with estimated calorie counts
- Workout plans tailored to fitness level
- Practical weight loss tips and motivation

Keep responses concise and actionable. For food suggestions always include approximate calories.
For workouts include duration and estimated calories burned."""

_NUTRITION_FORMAT = (
    "Respond with exactly these sections:\n\n"
    "**Food identified:** <what was eaten and estimated portion size>\n\n"
    "**Estimated calories:** <number> cal\n\n"
    "**Nutrition breakdown:**\n"
    "- Protein: ~Xg\n"
    "- Carbohydrates: ~Xg\n"
    "- Fat: ~Xg\n"
    "- Fiber: ~Xg\n\n"
    "**Health feedback:** <2-3 sentences on quality, balance, and one actionable suggestion>"
)


def _profile_context(profile) -> str:
    """Format a user profile row as a text block for injection into AI prompts."""
    if not profile:
        return ""
    lines = ["User profile:"]
    if profile["name"]:
        lines.append(f"- Name: {profile['name']}")
    if profile["age"]:
        lines.append(f"- Age: {profile['age']}")
    if profile["gender"]:
        lines.append(f"- Gender: {profile['gender']}")
    if profile["height_cm"]:
        lines.append(f"- Height: {profile['height_cm']:.0f} cm")
    if profile["goal_weight_lbs"]:
        lines.append(f"- Goal weight: {profile['goal_weight_lbs']:.1f} lbs")
    if profile["target_date"]:
        lines.append(f"- Target date: {profile['target_date']}")
    if profile["activity_level"]:
        lines.append(f"- Activity level: {profile['activity_level']}")
    if profile["dietary_prefs"]:
        lines.append(f"- Dietary preferences: {profile['dietary_prefs']}")
    if profile["allergies"]:
        lines.append(f"- Allergies/avoid: {profile['allergies']}")
    return "\n".join(lines)


def _build_system(profile) -> list[dict]:
    """Return a system prompt block, including profile context when available."""
    ctx = _profile_context(profile)
    text = f"{SYSTEM_PROMPT}\n\n{ctx}" if ctx else SYSTEM_PROMPT
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def chat(messages: list[dict], profile=None) -> str:
    response = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=1024,
        system=_build_system(profile),
        messages=messages,
    )
    return response.content[0].text


def analyze_food_text(description: str, profile=None) -> str:
    ctx = _profile_context(profile)
    prefix = f"{ctx}\n\n" if ctx else ""
    response = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                f"{prefix}I ate: {description}\n\n"
                f"Estimate the calories and nutrition for this meal. {_NUTRITION_FORMAT}"
            ),
        }],
    )
    return response.content[0].text


def analyze_food_image(image_bytes: bytes, media_type: str, profile=None) -> str:
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    ctx = _profile_context(profile)
    extra = f"\n\nUser context: {ctx}" if ctx else ""
    response = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": image_data,
                    },
                },
                {
                    "type": "text",
                    "text": (
                        f"Analyze this food image. {_NUTRITION_FORMAT}\n\n"
                        "If the image does not contain food, say so briefly."
                        f"{extra}"
                    ),
                },
            ],
        }],
    )
    return response.content[0].text


def generate_meal_plan(
    calories: int,
    period: str,
    dietary_prefs: str,
    allergies: str,
    ingredients: str = "",
    profile=None,
) -> str:
    # Fall back to profile defaults when form fields are left blank
    if not dietary_prefs.strip() and profile and profile["dietary_prefs"]:
        dietary_prefs = profile["dietary_prefs"]
    if not allergies.strip() and profile and profile["allergies"]:
        allergies = profile["allergies"]

    period_label = "one day" if period == "Daily" else "one week (7 days)"
    prefs = dietary_prefs.strip() or "none"
    allergy_note = f"Allergies/avoid: {allergies.strip()}" if allergies.strip() else "No allergies."

    profile_note = f"\n{_profile_context(profile)}\n" if profile else ""

    if ingredients.strip():
        ingredient_section = f"""Available ingredients the user has on hand:
{ingredients.strip()}

Rules for using ingredients:
- Prioritise meals that use these ingredients creatively and efficiently.
- Minimise waste: if an ingredient appears on Day 1, use leftovers on Day 2 where sensible.
- If the available ingredients are insufficient for a full {period_label} plan, suggest the minimal extra items needed (mark them with *) and explain why at the end.
- Never suggest a meal that requires an ingredient the user doesn't have without marking it with *.
"""
    else:
        ingredient_section = "No specific ingredients provided — suggest balanced, practical meals.\n"

    prompt = f"""Create a {period_label} meal plan targeting {calories} calories per day.
Dietary preferences: {prefs}
{allergy_note}
{profile_note}
{ingredient_section}
Format each day as:
**Day N** (or **Today** for a single day)
- Breakfast: <meal> (~<cal> cal)
- Lunch: <meal> (~<cal> cal)
- Dinner: <meal> (~<cal> cal)
- Snacks: <meal> (~<cal> cal)
- Daily total: ~<cal> cal

After the plan:
- If ingredients were provided, add a **Shopping list** section listing only the extra items marked with *.
- Add one **Tip** line for staying on track."""

    response = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=2048,
        system=_build_system(profile),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
