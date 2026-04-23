import base64

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic()

# Haiku is ~20x cheaper than Sonnet and sufficient for coaching tasks.
# Image analysis stays on the same model — Haiku supports vision too.
CHAT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You are a supportive AI weight loss coach. You help users with:
- Food suggestions with estimated calorie counts
- Workout plans tailored to fitness level
- Practical weight loss tips and motivation

Keep responses concise and actionable. For food suggestions always include approximate calories.
For workouts include duration and estimated calories burned."""

# Cached system prompt block — Anthropic charges 90% less on cache hits.
# The cache is valid for 5 minutes; repeated chat turns within that window are cheap.
_CACHED_SYSTEM = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def chat(messages: list[dict]) -> str:
    response = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=1024,
        system=_CACHED_SYSTEM,
        messages=messages,
    )
    return response.content[0].text


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


def analyze_food_text(description: str) -> str:
    response = client.messages.create(
        model=CHAT_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": (
                f"I ate: {description}\n\n"
                f"Estimate the calories and nutrition for this meal. {_NUTRITION_FORMAT}"
            ),
        }],
    )
    return response.content[0].text


def analyze_food_image(image_bytes: bytes, media_type: str) -> str:
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
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
) -> str:
    period_label = "one day" if period == "Daily" else "one week (7 days)"
    prefs = dietary_prefs.strip() or "none"
    allergy_note = f"Allergies/avoid: {allergies.strip()}" if allergies.strip() else "No allergies."

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
        system=_CACHED_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
