import base64

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic()

SYSTEM_PROMPT = """You are a supportive AI weight loss coach. You help users with:
- Food suggestions with estimated calorie counts
- Workout plans tailored to fitness level
- Practical weight loss tips and motivation

Keep responses concise and actionable. For food suggestions always include approximate calories.
For workouts include duration and estimated calories burned."""


def chat(messages: list[dict]) -> str:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    return response.content[0].text


def analyze_food_image(image_bytes: bytes, media_type: str) -> str:
    image_data = base64.standard_b64encode(image_bytes).decode("utf-8")
    response = client.messages.create(
        model="claude-sonnet-4-6",
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
                        "Analyze this food image and respond with exactly these sections:\n\n"
                        "**Food identified:** <what you see>\n\n"
                        "**Estimated calories:** <number> cal\n\n"
                        "**Nutrition breakdown:**\n"
                        "- Protein: ~Xg\n"
                        "- Carbohydrates: ~Xg\n"
                        "- Fat: ~Xg\n"
                        "- Fiber: ~Xg\n\n"
                        "**Health feedback:** <2-3 sentences on quality, balance, and one actionable suggestion>\n\n"
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
) -> str:
    period_label = "one day" if period == "Daily" else "one week (7 days)"
    prefs = dietary_prefs if dietary_prefs.strip() else "none"
    allergy_note = f"Allergies/avoid: {allergies}" if allergies.strip() else "No allergies."

    prompt = f"""Create a {period_label} meal plan with a target of {calories} calories per day.
Dietary preferences: {prefs}
{allergy_note}

Format each day as:
**Day N** (or **Today** for a single day)
- Breakfast: <meal> (~<cal> cal)
- Lunch: <meal> (~<cal> cal)
- Dinner: <meal> (~<cal> cal)
- Snacks: <meal> (~<cal> cal)
- Daily total: ~<cal> cal

End with a short tip for staying on track."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
