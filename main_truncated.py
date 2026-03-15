import os
from datetime import date
from typing import Optional, Literal

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

import functions

load_dotenv()

app = FastAPI(
    title="AutoBlog Agent System",
    description="Each user gets up to 3 automated blog agents. URL: /api/{user_id}/{agent_id}/blog/today",
    version="3.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "changeme")

# ── Auth ──────────────────────────────────────────────────────────────────────

def require_admin(x_admin_secret: str = Header(...)):
    if x_admin_secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin secret")


# ── Types ─────────────────────────────────────────────────────────────────────

ScenarioType     = Literal["website", "themed", "ecommerce", "news", "tutorial", "personal_brand", "seo_blitz", "affiliate"]
FrequencyType    = Literal["daily", "weekly", "bi-weekly", "3x-week", "2x-week", "monthly"]
ContentLengthType = Literal["short", "medium", "long", "longform"]
ToneType         = Literal["professional", "casual", "educational", "humorous", "inspirational", "journalistic"]


# ── Request models ────────────────────────────────────────────────────────────

class CreateAgentRequest(BaseModel):
    name: str = "My Blog Agent"               # human-friendly label
    scenario: ScenarioType = "themed"
    website_url: Optional[str] = None
    themes: Optional[list[str]] = None
    duration_months: float = 1.0              # 0.5 – 12
    frequency: FrequencyType = "weekly"
    content_length: ContentLengthType = "medium"
    tone: ToneType = "professional"
    audience: str = "general audience"
    language: str = "English"
    brand_name: Optional[str] = None
    brand_description: Optional[str] = None


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/", tags=["Health"])
def root():
    return {
        "status": "AutoBlog Agent System running",
        "version": "3.0.0",
        "agent_limit_per_user": functions.AGENT_LIMIT,
        "scenarios": list(functions.SCENARIO_FOCUS.keys()),
        "frequencies": list(functions.FREQUENCY_TO_DAYS.keys()),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  USER STATS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/{user_id}/stats", tags=["User"])
def get_user_stats(user_id: str):
    """
    Get a user's stats: agent count, limit, total blogs generated, etc.
    """
    stats = functions.ensure_user_stats(user_id)
    return {
        "user_id": user_id,
        "agent_count": stats["agent_count"],
        "agent_limit": stats["agent_limit"],
        "agents_remaining": stats["agent_limit"] - stats["agent_count"],
        "total_blogs_generated": stats["total_blogs_generated"],
        "total_posts_scheduled": stats["total_posts_scheduled"],
        "can_create_agent": stats["agent_count"] < stats["agent_limit"],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  AGENT ENDPOINTS  —  /api/{user_id}/agents/...
# ══════════════════════════════════════════════════════════════════════════════

@app.post("/api/{user_id}/agents", tags=["Agents"])
def create_agent(user_id: str, request: CreateAgentRequest):
    """
    Create a new blog agent for a user (max 3 per user).

    The agent builds a full content schedule and then auto-generates posts
    daily when you call  GET /api/{user_id}/{agent_id}/blog/today

    - `name`: a friendly label for this agent (e.g. "Tech Blog")
    - `scenario`: one of 8 content strategies
    - Pass `website_url`, `themes`, or both — at least one required
    - `duration_months`: 0.5 to 12
    - `frequency`: daily | weekly | bi-weekly | 3x-week | 2x-week | monthly
    """
    if not request.website_url and not request.themes:
        raise HTTPException(status_code=400, detail="Provide website_url, themes, or both.")
    if not (0.5 <= request.duration_months <= 12):
        raise HTTPException(status_code=400, detail="duration_months must be between 0.5 and 12")

    try:
        result = functions.create_agent_and_schedule(
            user_id=user_id,
            agent_name=request.name,
            scenario=request.scenario,
            duration_months=request.duration_months,
            frequency=request.frequency,
            content_length=request.content_length,
            tone=request.tone,
            audience=request.audience,
            language=request.language,
            website_url=request.website_url,
            themes=request.themes,
            brand_name=request.brand_name,
            brand_description=request.brand_description,
        )
        agent = result["agent"]
        schedule = result["schedule"]
        stats = functions.get_user_stats(user_id)
        return {
            "message": f"Agent '{agent['name']}' created with {len(schedule)} scheduled posts",
            "agent_id": agent["id"],
            "agent": agent,
            "total_posts": len(schedule),
            "schedule_preview": schedule[:5],
            "agents_used": stats["agent_count"],
            "agents_remaining": stats["agent_limit"] - stats["agent_count"],
            "daily_endpoint": f"/api/{user_id}/{agent['id']}/blog/today",
        }
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/{user_id}/agents", tags=["Agents"])
def list_agents(user_id: str):
    """List all agents for a user along with their stats."""
    agents = functions.get_user_agents(user_id)
    stats = functions.ensure_user_stats(user_id)
    return {
        "user_id": user_id,
        "agents": agents,
        "agent_count": stats["agent_count"],
        "agent_limit": stats["agent_limit"],
        "agents_remaining": stats["agent_limit"] - stats["agent_count"],
        "total_blogs_generated": stats["total_blogs_generated"],
    }


@app.get("/api/{user_id}/{agent_id}", tags=["Agents"])
def get_agent(user_id: str, agent_id: str):
    """Get a specific agent's details and full schedule."""
    agent = functions.get_agent(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    schedule = functions.get_schedule(agent_id)
    published = sum(1 for s in schedule if s["status"] == "published")
    return {
        "agent": agent,
        "schedule": schedule,
        "total_posts": len(schedule),
        "published": published,
        "pending": len(schedule) - published,
        "daily_endpoint": f"/api/{user_id}/{agent_id}/blog/today",
    }


@app.delete("/api/{user_id}/{agent_id}", tags=["Agents"])
def delete_agent(user_id: str, agent_id: str):
    """
    Delete an agent (frees up one agent slot).
    This also deletes all its schedule entries and generated blogs.
    """
    agent = functions.get_agent(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    functions.delete_agent(agent_id, user_id)
    return {"message": f"Agent '{agent['name']}' deleted. You now have a free slot."}


# ══════════════════════════════════════════════════════════════════════════════
#  THE BLOG ENDPOINT — embed this in your project
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/api/{user_id}/{agent_id}/blog/today", tags=["Blog"])
def get_todays_blog(user_id: str, agent_id: str, target_date: Optional[str] = None):
    """
    ## The one endpoint to embed in your project.

    Call daily → returns today's AI-written blog post.

    - Exact date match → serves that post
    - No exact match (weekly/bi-weekly plan) → serves most recent past post
    - Already generated → instant cache from Supabase
    - Not yet generated → Gemini writes it, stores it, returns it

    **Optional:** `?target_date=YYYY-MM-DD` (defaults to today)
    """
    agent = functions.get_agent(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    target = target_date or str(date.today())
    try:
        blog = functions.get_or_generate_blog(agent_id, user_id, target)
        if "error" in blog:
            raise HTTPException(status_code=404, detail=blog["error"])
        return blog
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/{user_id}/{agent_id}/blog/latest", tags=["Blog"])
def get_latest_blog(user_id: str, agent_id: str):
    """Get the most recently published blog post for an agent."""
    agent = functions.get_agent(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    blog = functions.get_latest_blog(agent_id)
    if not blog:
        raise HTTPException(status_code=404, detail="No published posts yet")
    return blog


@app.get("/api/{user_id}/{agent_id}/schedule", tags=["Blog"])
def get_agent_schedule(user_id: str, agent_id: str):
    """Get the full post schedule for an agent."""
    agent = functions.get_agent(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    schedule = functions.get_schedule(agent_id)
    return {"agent": agent, "schedule": schedule, "total": len(schedule)}


@app.get("/api/{user_id}/{agent_id}/blogs", tags=["Blog"])
def list_generated_blogs(user_id: str, agent_id: str):
    """List all previously generated blog posts for an agent."""
    agent = functions.get_agent(agent_id, user_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    result = (
        functions.supabase.table("generated_blogs")
        .select("id,agent_id,user_id,scheduled_date,title,meta_description,tags,reading_time_minutes,created_at")
        .eq("agent_id", agent_id)
        .eq("user_id", user_id)
        .order("scheduled_date", desc=True)
        .execute()
    )
    return {"blogs": result.data, "total": len(result.data)}


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/admin/agents", tags=["Admin"], dependencies=[Depends(require_admin)])
def admin_list_all_agents():
    """List all agents across all users (admin only)."""
    result = functions.supabase.table("agents").select("*").order("created_at", desc=True).execute()
    return {"agents": result.data, "total": len(result.data)}


@app.patch("/admin/user/{user_id}/limit", tags=["Admin"], dependencies=[Depends(require_admin)])
def admin_set_user_limit(user_id: str, limit: int):
    """Override the agent limit for a specific user (admin only)."""
    functions.supabase.table("user_stats").update({"agent_limit": limit}).eq("user_id", user_id).execute()
    return {"message": f"Agent limit for {user_id} set to {limit}"}
