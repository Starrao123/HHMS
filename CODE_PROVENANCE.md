# Student Info
### Name: Vinayak Rao
### NetID: 34206447
### Date: 11/12/2025

# Libraries and Depedencies

The most signifiant library I used was Twilio, which is the premiere messaging API. Based on my research, Twilio is robust and also offers free credits for open source usage, which makes it appropriate for my proejct. All of the other libraries and depencies I used are standard for this ourse and used in other assignments.

# Tools Used
- ChatGPT: via Web, GPT 4o
- GitHub Copilot: via VSCode extension w/ inline suggestions, GPT Mini 4o

# Prompts and Outputs

Note: these were all accessed on November 12th and November 13th.

# Code Sources

I did not reference any code sources outside of AI tools with the exception of my previous assignments for this course, most notably homework 3. I also referenced various lectures like the orchestration lectures, but since those are direct course materials, I don't think there is a need to call those out.

# AI Usage

Dates: November 12th and November 13th

## 1. Health Check Architecture Design
- **Purpose**: Understand how system-wide health checks should be structured across microservices.
- **Prompt**: "What is the flow of health check information in my application? As in system health check? I need it for a design document."
- **Output Used**: Detailed explanation of top‑down aggregation, service dependency mapping, and health propagation strategy.
- **File and Lines**: N/A
- **Modifications**: Added a section describing dependency‑aware health aggregation.

## 2. Pydantic Models for Health Checks
- **Purpose**: Define structured health check responses for all services.
- **Prompt**: "Also, how can I use pydantic for these health checkpoints? Each service has its own model.py for that reason."
- **Output Used**: Provided `HealthCheckResponse` and `Dependency` Pydantic models.
- **File and Lines**: `*/models.py`
- **Modifications**: Implemented standardized response schemas.

## 3. Full Health Check Endpoint Templates
- **Purpose**: Get working FastAPI `/health` endpoints for each microservice.
- **Prompt**: "Ok, now with this model, generate health checks for each service."
- **Output Used**: Initial generic endpoint template.
- **File and Lines**: `main.py` files
- **Modifications**: Implemented health check logic and structured responses.

## 4. No‑Helper Health Checks
- **Purpose**: Remove shared helper utilities and inline all dependency logic.
- **Prompt**: "Do it without helpers."
- **Output Used**: Rewrote service endpoints with direct inline checks.
- **File and Lines**: `main.py` files
- **Modifications**: Removed helper imports, replaced with direct DB/Redis/httpx calls.

## 5. User‑Service Health Check Design
- **Purpose**: Connect user-service health to Redis and postgres-user.
- **Prompt**: "Ok, what should the health route for user-service be? How can it use httpx to check the health of redis and also postgres-user?"
- **Output Used**: A route that uses Redis ping and httpx requests.
- **File and Lines**: `user-service/main.py:1–120`
- **Modifications**: Added Redis connectivity check and downstream httpx call.

## 6. Patient‑Data Service Health Check
- **Purpose**: Add checks for TimescaleDB, Redis, and optionally user-service.
- **Prompt**: "Ok now what about for patient-data-service"
- **Output Used**: Provided full FastAPI health check implementation.
- **File and Lines**: `patient-data-service/main.py:1–140`
- **Modifications**: Added Timescale connection test, Redis ping, and user-service dependency.

## 7. Analytics Service Health Check
- **Purpose**: Add health checks for dependencies relevant to analytics.
- **Prompt**: "What about for analytics service?"
- **Output Used**: Provided example health logic template.
- **File and Lines**: `analytics-service/main.py`
- **Modifications**: Added httpx checks and local compute‑engine status.

## 8. Alerts Service Health Check
- **Purpose**: Implement a health check for alert handling microservice.
- **Prompt**: "What about for alerts-service"
- **Output Used**: Provided alerts health logic using Redis and analytics-service checks.
- **File and Lines**: `alerts-service/main.py`
- **Modifications**: Added Redis and dependent analytics health queries.

## 9. Docker Healthcheck Issue Explanation
- **Purpose**: Understand why `dependency failed to start: container postgres-user has no healthcheck configured`
- **Prompt**: "dependency failed to start: container postgres-user has no healthcheck configured"
- **Output Used**: Explanation and recommended fix (add HEALTHCHECK to docker-compose).
- **File and Lines**: `docker-compose.yml`
- **Modifications**: Added a healthcheck block to postgres-user.

## 10. Choosing Dependencies for Health Checks
- **Purpose**: Decide which services should check which dependencies.
- **Prompt**: "Should patient-data also healthcheck user service?"
- **Output Used**: Explanation of loose coupling and conditional dependency.
- **File and Lines**: `patient-data-service/main.py`
- **Modifications**: Added (optional) httpx call to user-service.

## 11. Adding httpx as a Dependency
- **Purpose**: Verify if httpx needs to be added to requirements.
- **Prompt**: "Do I need to add httpx to requirements?"
- **Output Used**: Confirmed yes.
- **File and Lines**: `*/requirements.txt`
- **Modifications**: Added `httpx`.

## 12. Provenance Documentation Formatting
- **Purpose**: Create a structured provenance log in markdown.
- **Prompt**: "I need to document AI usage from this chat. I want it in a raw markdown format as shown below"
- **Output Used**: Template with Purpose/Prompt/Output/File/Modifications format.
- **File and Lines**: `provenance.md`
- **Modifications**: This file itself.
## 13. Code Review Request: "How does my code look?"
- **Purpose**: Solicit a review of `alerts-service/main.py` and related models for correctness and style.
- **Prompt**: "How does my code look?" (focused on `alerts-service/main.py`).
- **Output Used**: Code review notes, suggested fixes for imports, pydantic models, and cleanup recommendations.
- **File and Lines**: `alerts-service/main.py`, `alerts-service/models.py`
- **Modifications**: Adjusted imports and model types where applicable; documented suggestions in the repo.

## 14. Find Unused Requirements
- **Purpose**: Identify unused/ unnecessary pinned packages in service `requirements.txt` files.
- **Prompt**: "Are there any unecessary requirements I have? Like requirements I don't use?"
- **Output Used**: A report listing candidate unused requirements per service with rationale.
- **File and Lines**: `*/requirements.txt` files
- **Modifications**: Recommendations produced; follow-up edits applied to remove unused packages.

## 15. Apply Removals to Requirements
- **Purpose**: Apply the agreed removals to `requirements.txt` files across services.
- **Prompt**: "do all of the same removals"
- **Output Used**: Patch edits removing the unused package lines.
- **File and Lines**: `alerts-service/requirements.txt`, `user-service/requirements.txt`, `analytics-service/requirements.txt`, `patient-data-service/requirements.txt`
- **Modifications**: Removed `twilio`, `SQLAlchemy`, `pandas`, `numpy`, `python-dotenv`, and `httpx` where agreed.

## 16. Add AI prompts to provenance
- **Purpose**: Record the exact AI prompts used during this interactive session inside the provenance document.
- **Prompt**: "Can you document the AI prompts used in this chat under code provenance in the AI usage section? USe the same formatting as in the document"
- **Output Used**: This insertion — the list of chat prompts and short explanations now present in `CODE_PROVENANCE_ADDITIONS.md` and linked from the repository.
- **File and Lines**: `CODE_PROVENANCE_ADDITIONS.md`
- **Modifications**: New file containing entries 13–16 that mirror the formatting used in `CODE_PROVENANCE.md`.