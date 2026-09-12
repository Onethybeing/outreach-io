"""Version-1 prompt for every LLM node, plus the variables each node supplies (PLAN.md §8).

Templates use Jinja syntax: {{ variable }}. `required` must appear in every edited version;
`optional` may be used or left out, and renders as empty text when a node has no value, so use
`{{ x or "fallback" }}` or `{% if x %}`. `sample` validates edits and feeds "Test on sample".
"""

from dataclasses import dataclass, field

HEAVY_MODEL = "openai/gpt-oss-120b"
LIGHT_MODEL = "openai/gpt-oss-20b"

SAMPLE_PROFILE = (
    '{"name": "Priya Sharma", "current_role": "Machine Learning Engineer", "seniority": "mid", '
    '"years_experience": 4, "skills": ["Python", "PyTorch", "LLM fine-tuning", "RAG", "FastAPI"], '
    '"domains": ["AI infrastructure", "developer tools"], '
    '"target_titles": ["ML Engineer", "Applied AI Engineer"]}'
)


@dataclass(frozen=True)
class NodeContract:
    description: str
    required: tuple[str, ...]
    optional: tuple[str, ...]
    sample: dict[str, str]
    template: str
    model: str
    temperature: float
    variables: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "variables", self.required + self.optional)


NODES: dict[str, NodeContract] = {
    "parse_resume": NodeContract(
        description="Turns raw resume text into a structured candidate profile.",
        required=("resume_text",),
        optional=(),
        sample={
            "resume_text": "Priya Sharma, ML Engineer, 4 years. Built RAG pipelines and fine-tuned "
            "LLMs at a Series B dev-tools startup. Python, PyTorch, FastAPI.",
        },
        template="""You extract a structured profile from a resume.

Return only a JSON object with these keys:
- name (string)
- current_role (string)
- seniority: one of "junior", "mid", "senior", "lead", "executive"
- years_experience (number)
- skills (list of strings, most relevant first, max 15)
- domains (list of industries or problem areas the person has worked in)
- target_titles (list of 2-4 job titles this person is a strong fit for next)

Use only facts in the resume. If something is missing, use null or an empty list. Never guess.

Resume:
\"\"\"
{{ resume_text }}
\"\"\"""",
        model=HEAVY_MODEL,
        temperature=0.0,
    ),
    "build_search_queries": NodeContract(
        description="Writes Tavily search queries to find startups hiring for this profile.",
        required=("candidate_profile",),
        optional=("num_startups",),
        sample={"candidate_profile": SAMPLE_PROFILE, "num_startups": "5"},
        template="""You write web search queries to find startups where this candidate would be a strong hire.

Candidate profile (JSON):
{{ candidate_profile }}

We want about {{ num_startups or "5" }} startups in total.

Return only a JSON object: {"queries": [ ... ]} with 3 to 6 queries. Each query should target
early-to-growth-stage startups in the candidate's domains that plausibly need their target roles
(e.g. "Series A AI infrastructure startups hiring ML engineers"). No duplicates, no job-board names.""",
        model=HEAVY_MODEL,
        temperature=0.3,
    ),
    "discover_startups": NodeContract(
        description="Picks the most relevant startups from search results.",
        required=("candidate_profile", "search_results", "num_startups"),
        optional=("exclude_startups",),
        sample={
            "candidate_profile": SAMPLE_PROFILE,
            "search_results": '[{"title": "Acme Vector raises $12M Series A", "url": "https://example.com/acme", '
            '"content": "Acme Vector builds retrieval infrastructure for LLM apps and is growing its ML team."}]',
            "num_startups": "5",
            "exclude_startups": "Example Labs, Sample AI",
        },
        template="""You pick startups that fit a job candidate, from web search results.

Candidate profile (JSON):
{{ candidate_profile }}

Search results (JSON):
{{ search_results }}

Choose up to {{ num_startups }} distinct startups (not large public companies, not recruiters or
job boards) whose product or domain matches the candidate and who plausibly hire their target roles.
{% if exclude_startups %}Already found in earlier runs, do not pick again: {{ exclude_startups }}
{% endif %}
Return only a JSON object:
{"startups": [{"name": "", "website": "", "domain": "", "description": "one sentence",
"relevance": 0.0, "source_url": ""}]}
relevance is 0-1. Only include startups actually named in the results. Leave website empty if unknown.""",
        model=HEAVY_MODEL,
        temperature=0.0,
    ),
    "find_kdms": NodeContract(
        description="Picks the right decision-makers (founders, HR, hiring managers) at a startup.",
        required=("startup_name", "target_role", "search_results", "num_kdms"),
        optional=("startup_description",),
        sample={
            "startup_name": "Acme Vector",
            "startup_description": "Retrieval infrastructure for LLM apps.",
            "target_role": "ML Engineer",
            "search_results": '[{"title": "Jane Doe - Co-founder & CTO - Acme Vector | LinkedIn", '
            '"url": "https://www.linkedin.com/in/janedoe-example"}]',
            "num_kdms": "5",
        },
        template="""You identify the people at a startup who decide on hiring a {{ target_role }}.

Startup: {{ startup_name }}
{% if startup_description %}About: {{ startup_description }}
{% endif %}
Search results (JSON):
{{ search_results }}

Pick up to {{ num_kdms }} people, preferring in order: founders/CEO, CTO or relevant engineering
leader, head of talent/HR/recruiting, hiring manager for the role.

Rules:
- Only include people the results show working at {{ startup_name }}, with a LinkedIn profile URL
  (linkedin.com/in/...) copied exactly from the results.
- A different company with a similar name is not a match (e.g. "Nova Analytics" is not
  "Nova Labs", "Orbit Inc" is not "Orbit Robotics"). If the result's company isn't clearly
  {{ startup_name }}, skip the person.
- Use the title exactly as it appears in the result. If the result shows no title, use null.
  Never guess or write "inferred" / "likely".
- Skip people whose shown title is not a hiring decision-maker (e.g. individual-contributor
  engineers, interns, advisors, former employees). Returning fewer people is better than weak picks.

Return only a JSON object:
{"people": [{"name": "", "title": "", "linkedin_url": "", "why": "which result shows their role"}]}""",
        model=HEAVY_MODEL,
        temperature=0.0,
    ),
    "cross_check_company_match": NodeContract(
        description="Decides whether a scraped current employer is the same company as the startup.",
        required=("expected_company", "profile_company"),
        optional=("expected_website", "profile_title"),
        sample={
            "expected_company": "Acme Vector",
            "expected_website": "acmevector.example",
            "profile_company": "Acme Vector Inc.",
            "profile_title": "Co-founder & CTO",
        },
        template="""Decide if two company names refer to the same company.

Expected company: {{ expected_company }}{% if expected_website %} ({{ expected_website }}){% endif %}
Company on the person's current profile: {{ profile_company }}{% if profile_title %}, title: {{ profile_title }}{% endif %}

Treat legal suffixes (Inc, Ltd, Labs, AI, HQ), rebrands you are confident about, and parent/product
names as a match. Different companies with similar names are not a match.

Return only a JSON object: {"match": true|false, "confidence": 0.0, "reason": "one sentence"}""",
        model=LIGHT_MODEL,
        temperature=0.0,
    ),
    "research_startup": NodeContract(
        description="Turns search results about one company into a short factual brief for the drafts.",
        required=("startup_name", "search_results"),
        optional=("startup_website", "candidate_profile"),
        sample={
            "startup_name": "Acme Vector",
            "startup_website": "https://acmevector.example",
            "candidate_profile": SAMPLE_PROFILE,
            "search_results": (
                '[{"title": "Acme Vector raises $12M Series A", "url": "https://news.example/acme-a", '
                '"content": "Acme Vector, which builds retrieval infrastructure for LLM apps, raised a '
                '$12M Series A led by Example Ventures. The team of 18 is hiring backend and ML engineers."}]'
            ),
        },
        template="""Summarise what is known about one company, for someone writing them a short job-application email.

Company: {{ startup_name }}{% if startup_website %} ({{ startup_website }}){% endif %}
{% if candidate_profile %}
The person writing has this background (JSON). Note anything that genuinely overlaps:
{{ candidate_profile }}
{% endif %}
Search results (JSON):
{{ search_results }}

Use only what the search results state. Never guess funding, headcount, customers or dates. Leave a
field null or its list empty when the results don't support it. An empty brief is fine and useful.

Return only a JSON object with these keys:
- what_they_do: one sentence, concrete, in plain words (no marketing language)
- product: what they actually sell or build, or null
- stage: e.g. "seed", "Series A, $12M (2026)", or null
- team_size: a number or short phrase if stated, else null
- recent_news: up to 3 short factual items, each with "fact" and "url" taken from the results
- tech_signals: up to 5 technologies, methods or problem areas the company works on
- hiring_signals: what they say they're hiring for, or null
- overlap_with_candidate: up to 3 short, honest points connecting the candidate's stated experience
  to this company's work, only where both sides are supported. Empty list if there is no real overlap.

Return only the JSON object.""",
        model=LIGHT_MODEL,
        temperature=0.2,
    ),
    "generate_draft": NodeContract(
        description="Writes the personalized cold email to one contact.",
        required=("candidate_profile", "contact_name", "startup_name"),
        optional=("contact_title", "startup_description", "startup_brief", "contact_reason", "sender_name"),
        sample={
            "candidate_profile": SAMPLE_PROFILE,
            "contact_name": "Jane Doe",
            "contact_title": "Co-founder & CTO",
            "startup_name": "Acme Vector",
            "startup_description": "Retrieval infrastructure for LLM apps.",
            "startup_brief": (
                '{"what_they_do": "Builds retrieval infrastructure that LLM apps use to search their own data.", '
                '"product": "A hosted vector search API", "stage": "Series A, $12M (2026)", "team_size": 18, '
                '"recent_news": [{"fact": "Raised a $12M Series A led by Example Ventures", "url": "https://news.example/acme-a"}], '
                '"tech_signals": ["RAG", "vector search", "Python"], "hiring_signals": "backend and ML engineers", '
                '"overlap_with_candidate": ["Has built RAG pipelines in production"]}'
            ),
            "contact_reason": "Co-founder and CTO, owns engineering hiring at this size.",
            "sender_name": "Priya Sharma",
        },
        template="""Write a short cold email from a job candidate to a decision-maker at a startup.

Candidate profile (JSON):
{{ candidate_profile }}

Recipient: {{ contact_name }}{% if contact_title %}, {{ contact_title }}{% endif %} at {{ startup_name }}
{% if contact_reason %}Why them: {{ contact_reason }}
{% endif %}{% if startup_description %}What {{ startup_name }} does: {{ startup_description }}
{% endif %}{% if startup_brief %}
Researched notes on {{ startup_name }} (JSON, gathered from public sources):
{{ startup_brief }}
{% endif %}
Rules:
- 90 to 140 words. Plain text, no markdown, no emojis, no em dashes (write full stops or commas).
- Open with one specific, true connection between the candidate's experience and {{ startup_name }}'s work.
  Prefer something from the researched notes (their product, a recent development, or a listed
  overlap) over a generic compliment. Use at most one such detail, and only if the notes state it.
- Mention 1-2 concrete skills or results from the profile. Never invent achievements, numbers or companies.
- Every claim must be stated in the profile or the notes as-is. Don't merge separate items into one
  claim (e.g. a skill from one area applied to data from another domain) and don't imply experience
  in the recipient's industry unless the profile lists it.
- Don't flatter, don't restate their marketing back to them, and never claim to be a user or customer.
- Say the resume is attached. End with one low-effort ask (a short call, or who to talk to).
- Sign off as {{ sender_name or "the candidate" }}.

Return only a JSON object: {"subject": "under 8 words", "body": "the email"}""",
        model=HEAVY_MODEL,
        temperature=0.7,
    ),
    "classify_reply": NodeContract(
        description="Labels an inbound message on an outreach thread.",
        required=("reply_text",),
        optional=("sent_email",),
        sample={
            "sent_email": "Hi Jane, I build RAG pipelines ... resume attached.",
            "reply_text": "Thanks Priya, I'm out of office until Monday, will reply then.",
        },
        template="""Classify an inbound email received on a cold outreach thread.

{% if sent_email %}Our original email:
\"\"\"
{{ sent_email }}
\"\"\"
{% endif %}
Inbound message:
\"\"\"
{{ reply_text }}
\"\"\"

Labels:
- "reply": a human responded (interested, not interested, or a question)
- "bounce": delivery failure / undeliverable notice
- "out_of_office": automatic away message
- "unsubscribe": asks not to be contacted again

Return only a JSON object: {"label": "...", "summary": "one sentence"}""",
        model=LIGHT_MODEL,
        temperature=0.0,
    ),
    # --- eval judges (PLAN.md §11) ---------------------------------------------------------
    "eval_startup_relevance": NodeContract(
        description="Eval judge: scores how well a discovered startup fits the candidate (0-1).",
        required=("candidate_profile", "startup_name"),
        optional=("startup_description", "startup_domain"),
        sample={
            "candidate_profile": SAMPLE_PROFILE,
            "startup_name": "Acme Vector",
            "startup_description": "Retrieval infrastructure for LLM apps.",
            "startup_domain": "AI infrastructure",
        },
        template="""You are grading a job-search agent. Judge whether this startup is a good place for this
candidate to apply, based only on the information given.

Candidate profile (JSON):
{{ candidate_profile }}

Startup: {{ startup_name }}
{% if startup_domain %}Domain: {{ startup_domain }}
{% endif %}{% if startup_description %}What it does: {{ startup_description }}
{% endif %}
Scoring:
- 1.0: the startup's product/domain clearly needs the candidate's target roles and skills
- 0.5: adjacent domain, or the fit depends on details not shown
- 0.0: unrelated domain, not a startup, or not enough information to see any fit

Return only a JSON object: {"score": 0.0, "reason": "one sentence"}""",
        model=LIGHT_MODEL,
        temperature=0.0,
    ),
    "eval_draft_quality": NodeContract(
        description="Eval judge: scores a cold email draft and lists claims the profile doesn't support.",
        required=("candidate_profile", "email_subject", "email_body", "startup_name", "contact_name"),
        optional=("startup_description",),
        sample={
            "candidate_profile": SAMPLE_PROFILE,
            "email_subject": "RAG engineer for Acme",
            "email_body": "Hi Jane, I built RAG pipelines at a dev-tools startup. Resume attached. 15 minutes this week?",
            "startup_name": "Acme Vector",
            "startup_description": "Retrieval infrastructure for LLM apps.",
            "contact_name": "Jane Doe",
        },
        template="""You are grading a cold email a job candidate is about to send. Be strict.

Candidate profile (JSON), the only allowed source of facts about the candidate:
{{ candidate_profile }}

Recipient: {{ contact_name }} at {{ startup_name }}
{% if startup_description %}What {{ startup_name }} does: {{ startup_description }}
{% endif %}
Subject: {{ email_subject }}
Body:
\"\"\"
{{ email_body }}
\"\"\"

Grade:
- personalization (1-5): specific, true link between the candidate and this startup (5) vs generic (1)
- clarity (1-5): easy to read, one clear ask
- tone (1-5): professional, confident, not pushy or flattering
- length_ok: true if the body is roughly 60-160 words
- unsupported_claims: every statement about the candidate that the profile does not support,
  including separate facts merged into one claim. Empty list if none.
- score (0-1): overall readiness to send; any unsupported claim caps it at 0.4

Return only a JSON object:
{"personalization": 1, "clarity": 1, "tone": 1, "length_ok": true, "unsupported_claims": [], "score": 0.0, "reason": "one sentence"}""",
        model=HEAVY_MODEL,
        temperature=0.0,
    ),
}
