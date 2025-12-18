# Home Health Monitoring System (HHMS)
The Home Health Monitoring System (HHMS) continuously collects health data from patients’ devices, analyzes that data for anomalies, and alerts medical professionals when something is wrong. 

In the United States, the growing elderly population is driving increased need for long term patient care. A large sector of long term care is home care, where a patient remains at their residence and is supported by a family member or caretaker. Home care is beneficial because it reduces the load of health institutions like nursing homes while letting patients remain in a familiar environment; however, home care patients, especially those with chronic conditions, require continuous monitoring of their vital signs. Since they live at home, this cannot be done by medical professionals.

The HHMS addresses this problem by automatically monitoring patient vitals and alerting medical professionals only when their attention is required, thus improving overall care.

# Architecture Overview

## Services

1. **User Service**:
    * *Purpose*: Handles the core identity and relationship information regarding the users of the system, which are the patients and doctors. This service will handle registering users and obtaining information about them. 
    * This exists as a logical, separate service in order to couple the users of the system into a unified model, especially since patients and doctors would be associated with each other.
    * Example Routes:
        * POST /users/register
        * POST /users/{user_id}
2. Patient Data Service:
    * Purpose: Handles the ingestion of all vital signs and time-series health data from patient devices. 
    * This exists as a logical, separate service because the volume of patient data is much larger than the volume of user data, and the patient data service is very write heavy. Thus, it will require very different scaling than the user service. Additionally, the patient data collected may change and should be separate from the users themselves.
    * Example Routes:
        * POST /patient/{patient_id}
        * GET /patient/{patient_id}/latest
3. Analytics Service:
    * Purpose: Handles the analytics of patient data to detect anomalies
    * This exists as a logical, separate service because it is computationally intensive and will require a very different approach to scalability than data ingestion.
    * Example Routes:
        * POST /analytics/run
4. Alerting Service:
    * Purpose: Handles alerting via APIs like Twilio
    * This exists as a logical, separate service because it is logically separate from data ingestion and analysis and should be able to scale separately.
    * Example Routes:
        * GET /alerts/{patient_id}
        * POST /alerts/send/{patient_id}
5. Redis:
    * Purpose: Serves as an event bus between data ingestion and analytics. Also serves as a cache for frequent queries.
6. NGINX:
    * Purpose: Serves as an API gateway and load balancer for scalability


# Prerequisites

Minimum required software

- **Docker Engine** — required to run the services in containers. Docker Engine v20.10+ is recommended (the images and compose files were tested with Docker 20.10.x).
- **Docker Compose** — Compose v2 (the `docker compose` plugin) or `docker-compose` standalone v1.29+ is required to start the multi-container stack.
- **Python** — Python 3.11 is recommended for local development or running any helper scripts. (Services run inside containers using Python 3.11.)
- **Git** — to clone the repository.

Optional (developer convenience)

- `make` — optional convenience for running common tasks if Makefiles are added.
- `pyenv` / `venv` — recommended for managing a local Python environment.

Quick verification commands

Run these on your machine to confirm required tools are available:

```bash
docker --version      # e.g. Docker version 20.10.x
docker compose version # Compose plugin (preferred)
python3 --version     # e.g. Python 3.11.x
git --version
```

Quick install notes

- Install Docker and Compose: follow the official instructions for your OS; on Debian/Ubuntu a minimal sequence (see Docker docs for the canonical up-to-date steps) is:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg lsb-release
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" |
    sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io
```

- For Compose, prefer the `docker compose` plugin shipped with recent Docker packages. If you need the standalone binary, install `docker-compose` >= 1.29 following official docs.

Ports used by the stack (defaults)

- `8080:80` — nginx (external-facing proxy)
- `6379` — Redis (exposed in compose; services connect using the internal network)
- `5432` — Postgres/TimescaleDB containers (internal)

Start the stack

```bash
docker compose up --build
```

If you prefer the older CLI name, use `docker-compose up --build`.




# Installation and Setup

Run the system:

```bash
docker-compose up
```

Check the system:

```bash
docker ps
```

Shutdownt the system:
```bash
docker-compose down

## Environment Variables

This project reads configuration from `.env` (loaded by Docker Compose). A working default is already baked into `docker-compose.yml`, but for production or custom setups you should create your own `.env`.

Quick start:

```bash
cp .env.example .env
# Edit .env as needed (DB passwords, Twilio, etc.)
```

Notes:
- Set `TWILIO_TEST_MODE=true` to avoid sending real SMS while developing.
- Database hosts (`postgres-user`, `timescale-data`, `postgres-analytics`, `postgres-alerts`) refer to container names on the Docker network.
- If you provide `.env`, variables there override the defaults in `docker-compose.yml`.
```

# Usage Instructions

Information on how to check an access the health endpoints is described below underneath API Documentation. Another way of viewing health is by simply running `docker-compose up` and checking health with `docker ps`.

# API Documentation

## Health Endpoints

- Each service exposes `GET /health` which returns a JSON object matching the `HealthCheckResponse` pydantic model.
- On healthy: HTTP 200 with the health payload. On unhealthy: HTTP 503 with the same payload nested under `detail` (FastAPI HTTPException behavior).
- You can call services directly on the Docker network (e.g. `http://user-service:8000/health`) or via the nginx proxy (http://localhost:8080) using the paths below.

Common response schema (HealthCheckResponse)
- `service`: string (service name)
- `status`: `"healthy"` or `"unhealthy"`
- `dependencies`: object mapping dependency name -> Dependency
    - `Dependency` fields: `status` (string), `response_time_ms` (integer ms or null)

---

### User Service

- Path (internal): `GET http://user-service:8000/health`
- Path (via nginx): `GET http://localhost:8080/users/health`

Request (curl):
```
curl http://localhost:8080/users/health
```

Healthy (200):
```json
{
    "service": "user-service",
    "status": "healthy",
    "dependencies": {
        "postgres-user": { "status": "healthy", "response_time_ms": 12 },
        "redis": { "status": "healthy", "response_time_ms": 5 }
    }
}
```

Unhealthy (503):
```json
{
    "detail": {
        "service": "user-service",
        "status": "unhealthy",
        "dependencies": {
            "postgres-user": { "status": "healthy", "response_time_ms": 11 },
            "redis": { "status": "unhealthy", "response_time_ms": null }
        }
    }
}
```

---

### Analytics Service

- Path (internal): `GET http://analytics-service:8000/health`
- Path (via nginx): `GET http://localhost:8080/analytics/health`

Request (curl):
```
curl http://localhost:8080/analytics/health
```

## Code Quality

This repository includes formatting and linting configuration to keep code consistent across services:

- Formatting: Black and isort (configured in pyproject.toml)
- Linting: Ruff

Usage (local development):

```bash
pip install black isort ruff
chmod +x scripts/format.sh scripts/lint.sh
scripts/lint.sh   # run checks
scripts/format.sh # auto-fix formatting/imports
```

These tools are for developer convenience and are not required inside the service containers.

## End-to-End Test

Demonstrate the full pipeline (users + patient data + analytics + alerts):

1. Start services:

```bash
docker compose up -d
```

2. Run E2E script (uses the gateway at http://localhost:8080):

```bash
chmod +x scripts/e2e.sh
scripts/e2e.sh
```

What it does:
- Registers a doctor and a patient via `/users/register`
- Links the patient to the doctor via `/users/relationships`
- Sets a heart rate threshold via `/analytics/thresholds`
- Ingests an abnormal reading via `/patient/{patient_id}`
- Verifies an alert exists in `/alerts/{patient_id}` (Twilio in TEST mode)

Expected output ends with: `PASS: Alert created (id=...) for patient ...`

Healthy (200):
```json
{
    "service": "analytics-service",
    "status": "healthy",
    "dependencies": {
        "postgres-analytics": { "status": "healthy", "response_time_ms": 18 },
        "redis": { "status": "healthy", "response_time_ms": 6 }
    }
}
```

Unhealthy (503):
```json
{
    "detail": {
        "service": "analytics-service",
        "status": "unhealthy",
        "dependencies": {
            "postgres-analytics": { "status": "unhealthy", "response_time_ms": null },
            "redis": { "status": "healthy", "response_time_ms": 7 }
        }
    }
}
```

---

### Alerts Service

- Path (internal): `GET http://alerts-service:8000/health`
- Path (via nginx): `GET http://localhost:8080/alerts/health`

Request (curl):
```
curl http://localhost:8080/alerts/health
```

Healthy (200):
```json
{
    "service": "alerts-service",
    "status": "healthy",
    "dependencies": {
        "redis": { "status": "healthy", "response_time_ms": 3 }
    }
}
```

Unhealthy (503):
```json
{
    "detail": {
        "service": "alerts-service",
        "status": "unhealthy",
        "dependencies": {
            "redis": { "status": "unhealthy", "response_time_ms": null }
        }
    }
}
```

---

#### Sending Alerts

Two endpoints are available:

- Primary: `POST http://localhost:8080/alerts/notifications/send`
    Body:
    `{ "patient_id": "<uuid>", "message": "...", "severity": "info|warning|critical" }`

- Compatibility: `POST http://localhost:8080/alerts/send/{patient_id}`
    Body requires only `message` and `severity`; `patient_id` is taken from the path.

Twilio configuration for real SMS:

- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM_NUMBER`

Local testing (no real SMS):

- Set `TWILIO_TEST_MODE=true` to mark alerts as SENT with a mock provider ID.

### Patient Data Service

- Path (internal): `GET http://patient-data-service:8000/health`
- Path (via nginx): `GET http://localhost:8080/patient/health`

Request (curl):
```
curl http://localhost:8080/patient/health
```

Healthy (200):
```json
{
    "service": "patient-data-service",
    "status": "healthy",
    "dependencies": {
        "timescale-data": { "status": "healthy", "response_time_ms": 22 },
        "redis": { "status": "healthy", "response_time_ms": 4 }
    }
}
```

Unhealthy (503):
```json
{
    "detail": {
        "service": "patient-data-service",
        "status": "unhealthy",
        "dependencies": {
            "timescale-data": { "status": "unhealthy", "response_time_ms": null },
            "redis": { "status": "healthy", "response_time_ms": 5 }
        }
    }
}
```

---

### Nginx

- Health path (proxy): `GET http://localhost:8080/health`
- Response: HTTP 200 plain text `ok`

# Testing

Use the provided smoke script to verify core routes via nginx.

```bash
docker compose up -d --build
bash scripts/smoke.sh
```

The script checks:
- Gateway and service health endpoints
- User registration (accepts `name` alias)
- Patient latest before/after ingestion
- Analytics run trigger
- Alerts send (compat route) and history

### Twilio & Anomaly Detection Test

Run an integrated test that verifies Twilio notifications (test mode) and analytics anomaly detection end-to-end:

```bash
docker compose up -d --build
bash scripts/test_alerts_anomalies.sh
```

What it does:
- Health checks for all services
- Registers a patient
- Sends an alert via `alerts/notifications/send` (uses `TWILIO_TEST_MODE` to mark as `sent`)
- Creates a heart_rate threshold, ingests violating vitals, and verifies anomalies are recorded
- Confirms alerts history contains the anomaly-triggered alert

### Real SMS Test

To send a real SMS to a specific phone when an anomaly occurs, configure Twilio and run:

```bash
# 1) Set Twilio credentials in .env and disable test mode
sed -i '' 's/TWILIO_TEST_MODE=.*/TWILIO_TEST_MODE=false/' .env
docker compose up -d

# 2) Run the real SMS test (pass your phone in E.164 format)
bash scripts/test_real_sms.sh +15555550123 "Hello from HHMS"
```

Requirements:
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` must be valid.
- Phone numbers must use E.164 format (e.g., `+15555550123`).
- The script registers a patient with your phone, triggers an anomaly, and checks alerts history.

# Project Structure

```
HHMS/
├── README.md
├── CODE_PROVENANCE.md
├── architecture-diagram.png
├── docker-compose.yml
├── alerts-service/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   └── models.py
├── analytics-service/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py
│   └── models.py
└── patient-data-service/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py
    └── models.py
── user-service/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py
    └── models.py
── nginx/
    ├── nginx.cong
```

* **README.md**: Contains overview of project
* **CODE_PROVENANCE.md**: Contains documentation of code sources and assistance
* **architecture-diagram.png**: Architectural diagram of project services
* **docker-compose.yml**: Manages Docker startup of various services
* **alerts-service**: Contains the alert service
* **analytics-service**: Contains the analytics service
* **patient-data-service**: Contains the patient data service
* **user-service**: Contains the user service







