import os
import json
import httpx
from bs4 import BeautifulSoup
from datetime import date, timedelta
from typing import Optional

import google.generativeai as genai
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# ── Clients ───────────────────────────────────────────────────────────────────

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini = genai.GenerativeModel("gemini-2.5-flash-lite")

supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY"),   # service_role key — bypasses RLS
)

AGENT_LIMIT = 3

# ── Constants ─────────────────────────────────────────────────────────────────

FREQUENCY_TO_DAYS: dict[str, int] = {
    "daily":     1,
    "2x-week":   4,
    "3x-week":   2,
    "weekly":    7,
    "bi-weekly": 14,
    "monthly":   30,
}

CONTENT_LENGTH_TO_WORDS: dict[str, int] = {
    "short":    500,
    "medium":   800,
    "long":     1500,
    "longform": 2500,
}

SCENARIO_FOCUS: dict[str, str] = {
    "website": "Analyse the provided website and create topically relevant, SEO-optimised content that matches the site's niche and audience.",
    "themed":  "Create a content calendar based on the provided themes, topics, and data the user supplied.",
    # Legacy keys kept so old agents still work
    "ecommerce":      "Focus on product showcases, buying guides, and review-style posts that drive purchase intent.",
    "news":           "Cover industry news, trend analyses, weekly roundups, and hot takes.",
    "tutorial":       "Write step-by-step how-to guides, beginner tutorials, and practical walkthroughs.",
    "personal_brand": "Create thought leadership pieces, personal stories, and expertise showcases.",
    "seo_blitz":      "Build a pillar-and-cluster SEO strategy targeting long-tail keywords.",
    "affiliate":      "Create affiliate-friendly content: reviews, 'best of' roundups, and comparisons.",
}

# ── Gemini helpers ────────────────────────────────────────────────────────────

def ask_gemini(prompt: str) -> str:
    response = gemini.generate_content(prompt)
    return response.text.strip()


def parse_json_from_gemini(text: str) -> dict | list:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1]
        clean = clean.rsplit("```", 1)[0]
    return json.loads(clean)


# ── Schedule date calculation ─────────────────────────────────────────────────

def calculate_post_dates(duration_months: float, frequency: str) -> list[str]:
    step_days = FREQUENCY_TO_DAYS[frequency]
    total_days = int(duration_months * 30)
    dates, current = [], date.today()
    while (current - date.today()).days < total_days:
        dates.append(str(current))
        current += timedelta(days=step_days)
    return dates


# ── Prompt builders ───────────────────────────────────────────────────────────

def build_plan_prompt(
    scenario: str,
    post_dates: list[str],
    website_url: Optional[str],
    themes: Optional[list[str]],
    tone: str,
    audience: str,
    content_length: str,
    brand_name: Optional[str],
    brand_description: Optional[str],
    language: str,
) -> str:
    word_count = CONTENT_LENGTH_TO_WORDS[content_length]
    focus = SCENARIO_FOCUS[scenario]
    context_parts = []
    if website_url:
        context_parts.append(f"Website to analyse: {website_url}")
    if themes:
        context_parts.append(f"Themes / topics: {', '.join(themes)}")
    if brand_name:
        context_parts.append(f"Brand name: {brand_name}")
    if brand_description:
        context_parts.append(f"Brand description: {brand_description}")
    context = "\n".join(context_parts)
    date_list = "\n".join(f"- {d}" for d in post_dates)

    return f"""
You are an expert content strategist.
Strategy focus: {focus}

Context:
{context}

Settings:
- Tone: {tone}
- Target audience: {audience}
- Language: {language}
- Word count per post: ~{word_count} words

Generate exactly {len(post_dates)} blog post ideas for these dates:
{date_list}

Return ONLY a valid JSON array (no markdown, no extra text). Each element:
{{
  "date": "YYYY-MM-DD",
  "title": "Compelling blog post title",
  "description": "2-3 sentence overview",
  "keywords": ["keyword1", "keyword2", "keyword3"],
  "word_count": {word_count}
}}
""".strip()


def build_content_prompt(
    scenario: str,
    title: str,
    description: str,
    keywords: list[str],
    word_count: int,
    tone: str,
    audience: str,
    brand_name: Optional[str],
    language: str,
) -> str:
    kw = ", ".join(keywords)
    brand_line = f"Brand: {brand_name}. Mention it naturally where relevant." if brand_name else ""

    scenario_instructions = {
        "website":        "Write as a subject-matter expert matching the site's niche. Include internal-link placeholders like [Link: related post].",
        "themed":         "Write an engaging, informative post that covers the topic thoroughly.",
        "ecommerce":      "Include a product recommendation section and a clear CTA to buy or explore.",
        "news":           "Use a journalistic style. Lead with the news hook. Include context and takeaways.",
        "tutorial":       "Use numbered steps with clear headings. Include prerequisites and a summary checklist.",
        "personal_brand": "Write in first person. Share a personal insight or story. End with a question to engage readers.",
        "seo_blitz":      "Optimise heavily for the primary keyword. Use it in H1, first paragraph, and subheadings. Include FAQ section.",
        "affiliate":      "Include a comparison table or pros/cons list. Add affiliate disclaimer at top. End with a recommendation.",
    }

    instruction = scenario_instructions.get(scenario, "Write an engaging, informative blog post.")

    return f"""
You are an expert blog writer.
Writing instructions: {instruction}
{brand_line}

Post details:
- Title: {title}
- Description: {description}
- Target keywords: {kw}
- Target word count: ~{word_count} words
- Tone: {tone}
- Audience: {audience}
- Language: {language}

Return ONLY a valid JSON object (no markdown):
{{
  "title": "Final post title",
  "meta_description": "150-160 character SEO meta description",
  "content": "Full blog post in markdown format",
  "tags": ["tag1", "tag2", "tag3"],
  "reading_time_minutes": 5
}}
""".strip()


# ── User stats ────────────────────────────────────────────────────────────────

def get_user_stats(user_id: str) -> dict | None:
    result = supabase.table("user_stats").select("*").eq("user_id", user_id).execute()
    return result.data[0] if result.data else None


def ensure_user_stats(user_id: str) -> dict:
    stats = get_user_stats(user_id)
    if not stats:
        result = supabase.table("user_stats").insert({"user_id": user_id}).execute()
        stats = result.data[0]
    return stats


def check_agent_limit(user_id: str) -> tuple[bool, dict]:
    """Returns (can_create, stats). can_create=False if at limit."""
    stats = ensure_user_stats(user_id)
    can_create = stats["agent_count"] < stats["agent_limit"]
    return can_create, stats


# ── Agent CRUD ────────────────────────────────────────────────────────────────

def get_agent(agent_id: str, user_id: str) -> dict | None:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("id", agent_id)
        .eq("user_id", user_id)
        .execute()
    )
    return result.data[0] if result.data else None


def get_user_agents(user_id: str) -> list[dict]:
    result = (
        supabase.table("agents")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return result.data


def delete_agent(agent_id: str, user_id: str):
    supabase.table("agents").delete().eq("id", agent_id).eq("user_id", user_id).execute()


# ── Schedule CRUD ─────────────────────────────────────────────────────────────

def create_schedule_entries(entries: list[dict]) -> list[dict]:
    result = supabase.table("agent_schedule").insert(entries).execute()
    return result.data


def get_schedule(agent_id: str) -> list[dict]:
    result = (
        supabase.table("agent_schedule")
        .select("*")
        .eq("agent_id", agent_id)
        .order("scheduled_date")
        .execute()
    )
    return result.data


def get_schedule_entry_for_date(agent_id: str, target_date: str) -> dict | None:
    result = (
        supabase.table("agent_schedule")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("scheduled_date", target_date)
        .execute()
    )
    return result.data[0] if result.data else None


def get_nearest_past_entry(agent_id: str, target_date: str) -> dict | None:
    result = (
        supabase.table("agent_schedule")
        .select("*")
        .eq("agent_id", agent_id)
        .lte("scheduled_date", target_date)
        .order("scheduled_date", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ── Blog CRUD ─────────────────────────────────────────────────────────────────

def get_generated_blog_by_date(agent_id: str, scheduled_date: str) -> dict | None:
    result = (
        supabase.table("generated_blogs")
        .select("*")
        .eq("agent_id", agent_id)
        .eq("scheduled_date", scheduled_date)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def get_latest_blog(agent_id: str) -> dict | None:
    result = (
        supabase.table("generated_blogs")
        .select("*")
        .eq("agent_id", agent_id)
        .order("scheduled_date", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def save_generated_blog(blog_data: dict) -> dict:
    result = supabase.table("generated_blogs").insert(blog_data).execute()
    return result.data[0]


def mark_schedule_published(schedule_id: str):
    supabase.table("agent_schedule").update({"status": "published"}).eq("id", schedule_id).execute()


# ── Orchestration ─────────────────────────────────────────────────────────────

def create_agent_and_schedule(
    user_id: str,
    agent_name: str,
    scenario: str,
    duration_months: float,
    frequency: str,
    content_length: str,
    tone: str,
    audience: str,
    language: str,
    website_url: Optional[str] = None,
    themes: Optional[list[str]] = None,
    brand_name: Optional[str] = None,
    brand_description: Optional[str] = None,
) -> dict:
    # 1. Enforce limit
    can_create, stats = check_agent_limit(user_id)
    if not can_create:
        raise ValueError(f"Agent limit reached ({stats['agent_limit']} max). Delete an existing agent to create a new one.")

    # 2. Create agent row
    agent_result = supabase.table("agents").insert({
        "user_id": user_id,
        "name": agent_name,
        "scenario": scenario,
        "website_url": website_url,
        "themes": themes or [],
        "tone": tone,
        "audience": audience,
        "language": language,
        "duration_months": duration_months,
        "frequency": frequency,
        "content_length": content_length,
        "brand_name": brand_name,
        "brand_description": brand_description,
        "status": "active",
    }).execute()
    agent = agent_result.data[0]

    # 3. Generate schedule dates (in Python, not Gemini)
    post_dates = calculate_post_dates(duration_months, frequency)

    # 4. Ask Gemini for titles/descriptions
    prompt = build_plan_prompt(
        scenario=scenario,
        post_dates=post_dates,
        website_url=website_url,
        themes=themes,
        tone=tone,
        audience=audience,
        content_length=content_length,
        brand_name=brand_name,
        brand_description=brand_description,
        language=language,
    )
    raw = ask_gemini(prompt)
    schedule_items = parse_json_from_gemini(raw)

    word_count = CONTENT_LENGTH_TO_WORDS[content_length]

    # 5. Bulk-insert schedule
    entries = []
    for i, item in enumerate(schedule_items):
        entries.append({
            "agent_id": agent["id"],
            "user_id": user_id,
            "scheduled_date": post_dates[i] if i < len(post_dates) else item.get("date"),
            "title": item["title"],
            "description": item["description"],
            "keywords": item.get("keywords", []),
            "word_count": item.get("word_count", word_count),
            "status": "pending",
        })
    schedule = create_schedule_entries(entries)

    return {"agent": agent, "schedule": schedule}



# ── Website Scraper ───────────────────────────────────────────────────────────

def scrape_website(url: str) -> dict:
    """
    Scrape a website URL and return structured content for AI analysis.
    Returns: { url, title, description, headings, body_text, word_count }
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AutoBlogBot/1.0; +https://autoblog.ai)",
        "Accept": "text/html,application/xhtml+xml",
    }

    try:
        resp = httpx.get(url, headers=headers, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.TimeoutException:
        raise ValueError(f"Request timed out fetching: {url}")
    except httpx.HTTPStatusError as e:
        raise ValueError(f"HTTP {e.response.status_code} when fetching: {url}")
    except Exception as e:
        raise ValueError(f"Failed to fetch URL: {e}")

    soup = BeautifulSoup(resp.text, "lxml")

    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
        tag.decompose()

    title = (soup.find("title") or soup.find("h1") or soup.find("h2"))
    title_text = title.get_text(strip=True) if title else ""

    meta_desc = soup.find("meta", attrs={"name": "description"})
    description = meta_desc["content"].strip() if meta_desc and meta_desc.get("content") else ""

    headings = []
    for tag in soup.find_all(["h1", "h2", "h3"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 3:
            headings.append(text)
    headings = headings[:20]

    main = soup.find("main") or soup.find("article") or soup.find("body")
    raw_text = main.get_text(separator=" ", strip=True) if main else soup.get_text(separator=" ", strip=True)

    # Collapse whitespace
    import re
    body_text = re.sub(r"\s+", " ", raw_text).strip()
    body_text = body_text[:5000]  # cap at 5k chars for Gemini

    word_count = len(body_text.split())

    return {
        "url": url,
        "title": title_text,
        "description": description,
        "headings": headings,
        "body_text": body_text,
        "word_count": word_count,
    }


def get_or_generate_blog(agent_id: str, user_id: str, target_date: str) -> dict:
    """
    Core logic for GET /api/{user_id}/{agent_id}/blog/today
    1. Find exact or nearest past schedule entry.
    2. If blog already generated → return cached.
    3. Otherwise → generate with Gemini, store, return.
    """
    entry = get_schedule_entry_for_date(agent_id, target_date)
    if not entry:
        entry = get_nearest_past_entry(agent_id, target_date)
    if not entry:
        return {"error": f"No blog scheduled on or before {target_date} for agent {agent_id}"}

    # Check cache by agent_id + scheduled_date (indexed, reliable)
    existing = get_generated_blog_by_date(agent_id, str(entry["scheduled_date"]))
    if existing:
        return existing

    agent = get_agent(agent_id, user_id) or {}

    prompt = build_content_prompt(
        scenario=agent.get("scenario", "themed"),
        title=entry["title"],
        description=entry["description"],
        keywords=entry.get("keywords") or [],
        word_count=entry.get("word_count", 800),
        tone=agent.get("tone", "professional"),
        audience=agent.get("audience", "general audience"),
        brand_name=agent.get("brand_name"),
        language=agent.get("language", "English"),
    )
    raw = ask_gemini(prompt)
    blog_data = parse_json_from_gemini(raw)

    saved = save_generated_blog({
        "agent_id": agent_id,
        "user_id": user_id,
        "schedule_id": entry["id"],
        "scheduled_date": entry["scheduled_date"],
        "title": blog_data.get("title", entry["title"]),
        "meta_description": blog_data.get("meta_description", ""),
        "content": blog_data.get("content", ""),
        "tags": blog_data.get("tags", []),
        "reading_time_minutes": blog_data.get("reading_time_minutes", 5),
    })

    mark_schedule_published(entry["id"])
    return saved


