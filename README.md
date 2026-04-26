# NutriCoach — AI-Powered Weight Loss App

An AI nutrition and weight loss coaching app built with Streamlit and Claude.

## Features

- **Food Analysis** — Upload a photo or describe a meal to get AI-estimated calories and macros (protein, carbs, fat, fiber)
- **Meal Planning** — Generate daily or weekly meal plans tailored to your calorie goals, dietary preferences, and available ingredients, with auto-generated shopping lists
- **Weight Tracking** — Log weight over time, view history, and track BMI and TDEE
- **User Profiles** — Store personal stats, fitness goals, activity level, and dietary restrictions that personalize every AI response
- **AI Chat** — Ask nutrition and fitness questions and get personalized coaching

## Tech Stack

| Layer | Technology |
|-------|------------|
| UI | Streamlit |
| AI | Claude Haiku 4.5 via Anthropic SDK |
| Database | SQLite |
| Auth | PBKDF2-HMAC-SHA256 + session tokens |

## Setup

### Prerequisites

- Python 3.11+
- An [Anthropic API key](https://console.anthropic.com/)

### Install & Run

```bash
git clone <repo-url>
cd ai-weight-loss-app
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The app runs at `http://localhost:8501`.

### Environment Variables

Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-...           # Required
INVITE_CODE=change-me-before-sharing   # Optional: restrict signups
DAILY_AI_LIMIT=20                      # Optional: max AI requests per user/day (default 20)
```

When deploying to Streamlit Cloud, set `ANTHROPIC_API_KEY` under **Settings → Secrets** instead.

## Dev Container

This repo includes a `.devcontainer` config for GitHub Codespaces. Open it in Codespaces and the environment will be set up automatically, with the app running on port 8501.

## Project Structure

```
ai-weight-loss-app/
├── streamlit_app.py      # Main UI (auth, all feature pages)
├── app/
│   ├── db.py             # SQLite database layer
│   └── services/ai.py    # Claude API integration
├── requirements.txt
├── .devcontainer/        # GitHub Codespaces config
└── data.db               # Local SQLite database (auto-created)
```
