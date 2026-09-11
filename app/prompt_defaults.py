"""Version-1 prompt for every LLM node, plus the variables each node supplies (PLAN.md §8).

Templates use Jinja syntax: {{ variable }}. `required` must appear in every edited version;
`optional` may be used or left out, and renders as empty text when a node has no value — use
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
            "resume_text": "Priya Sharma — ML Engineer, 4 years. Built RAG pipelines and fine-tuned "
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

Use only facts in the resume. If something is missing, use null or an empty list — never guess.

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
    "generate_draft": NodeContract(
        description="Writes the personalized cold email to one contact.",
        required=("candidate_profile", "contact_name", "startup_name"),
        optional=("contact_title", "startup_description", "sender_name"),
        sample={
            "candidate_profile": SAMPLE_PROFILE,
            "contact_name": "Jane Doe",
            "contact_title": "Co-founder & CTO",
            "startup_name": "Acme Vector",
            "startup_description": "Retrieval infrastructure for LLM apps.",
            "sender_name": "Priya Sharma",
        },
        template="""Write a short cold email from a job candidate to a decision-maker at a startup.

Candidate profile (JSON):
{{ candidate_profile }}

Recipient: {{ contact_name }}{% if contact_title %}, {{ contact_title }}{% endif %} at {{ startup_name }}
{% if startup_description %}What {{ startup_name }} does: {{ startup_description }}
{% endif %}
Rules:
- 90 to 140 words. Plain text, no markdown, no emojis.
- Open with one specific, true connection between the candidate's experience and {{ startup_name }}'s work.
- Mention 1-2 concrete skills or results from the profile. Never invent achievements, numbers or companies.
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
            "reply_text": "Thanks Priya — I'm out of office until Monday, will reply then.",
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
}
