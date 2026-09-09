<div align="center">

# 🤖 AI SRE Agent

### AI-Powered Site Reliability Engineering Platform for AWS Infrastructure

An intelligent observability platform that automatically collects AWS infrastructure telemetry and billing data, performs AI-driven Root Cause Analysis (RCA) and cost analysis using Google Gemini, and surfaces it all through a live API and dashboard for cloud operations teams.

---

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-Cloud-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Google-Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-API-009688?style=for-the-badge&logo=fastapi&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-Dashboard-FF4B4B?style=for-the-badge&logo=streamlit&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-Monitoring-F46800?style=for-the-badge&logo=grafana&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-Metrics-E6522C?style=for-the-badge&logo=prometheus&logoColor=white)

</div>

---

## 📖 Overview

AI SRE Agent is an intelligent cloud observability and incident analysis platform built for AWS environments. It runs **two independent pipelines**:

1. **Infrastructure Investigation** — continuously (or on demand) collects telemetry from EC2, Application Load Balancers, Auto Scaling Groups, and CloudTrail, transforms it into structured AI context, and uses Google Gemini to perform automated Root Cause Analysis — either across the whole account ("Full Investigation") or for one specific, user-picked resource ("Single Resource Investigation"). Every completed investigation can then be deepened, entirely on demand, with **Follow-Up Questions** (evidence-grounded Q&A about that specific RCA) and an **optional Log Investigation** (a bounded, sanitized log analysis stage that never runs automatically).
2. **Cost Explorer** — a completely separate pipeline that pulls real AWS Cost Explorer billing data (gross cost, credits, net cost, service/region breakdowns, cost anomalies) and uses Gemini to explain cost changes in plain language.

Both pipelines are exposed through a **FastAPI backend** and a **Streamlit dashboard**, so the whole platform is usable live, not just as a CLI script. Instead of manually reviewing CloudWatch metrics, Auto Scaling events, Load Balancer health, CloudTrail logs, and Cost Explorer reports, the AI SRE Agent correlates the signals and generates human-readable incident and cost reports with severity classification, root causes, evidence, and remediation recommendations.

The project follows a modular architecture, making it easy to extend with additional AWS services, monitoring sources, notification channels, or AI models.

---

## 🚀 Key Features

**Infrastructure Investigation**
- 📊 Collect infrastructure telemetry from CloudWatch, ALB, Auto Scaling, and CloudTrail
- 📈 Metric trend history (unit, real CloudWatch alarm threshold when one exists, and hourly datapoints) fed to Gemini for every EC2/ALB/ASG resource
- 🔍 Full Infrastructure Investigation (every resource, one run) or Single Resource Investigation (one resource, on demand)
- ⚡ Live AWS resource discovery for the Single Resource picker — no need to run a Full Investigation first
- 🧠 AI-powered Root Cause Analysis using Google Gemini (severity, root cause, evidence, recommendations)
- 📄 Structured JSON incident reports, execution logging with retry/backoff, automatic archival per run

**Follow-Up Questions ("Ask AegisOps")**
- 💬 Ask evidence-grounded follow-up questions about any completed RCA, directly on the Investigation Report page
- 🔒 Answers are grounded only in that investigation's own sanitized evidence — Gemini is explicitly instructed never to answer from general knowledge and to say so plainly when the evidence is insufficient
- 🧾 Per-investigation conversation history persisted server-side (`output/conversations/`), survives page reloads
- 🛑 The original RCA/severity/report is immutable — a follow-up answer never overwrites it, and a question about a superseded (re-investigated) resource is rejected with a clear message

**Optional Log Investigation**
- 🔍 A "🔍 Investigate Logs" button on the Investigation Report page — logs are **never** collected automatically, only on explicit click
- 🪟 A bounded incident window is derived from the resource's own metric threshold breaches (never a hardcoded 24-hour scan)
- 📉 Local, deterministic reduction (time filter → relevance filter → normalize → deduplicate → aggregate into patterns → timeline → bounded representative samples) turns potentially millions of raw log lines into a compact evidence package before anything reaches Gemini
- 🧼 A **second, isolated** log sanitizer (separate from the infrastructure sanitizer) strips IPs, hostnames, URLs, auth headers, tokens, and connection strings while preserving timestamps, status codes, exception types, and counts
- ⚖️ Real, resource-specific log sources — CloudWatch Logs for EC2, S3-delivered access logs for ALB, scaling activities for Auto Scaling Groups — with an honest "logs unavailable" state that is never confused with "no errors found"
- 🧩 Gemini's log analysis is shown as a clearly-labeled *supplementary* result — it can propose an updated root cause/confidence, but the original RCA report is never silently overwritten

**Cost Explorer**
- 💳 Real AWS Cost Explorer data: gross cost, AWS credits applied, and net cost — clearly separated, never blended
- 🧾 Service- and region-level cost *and* credit attribution (a service fully offset by credits still shows its real usage instead of disappearing at $0)
- 🟠 AWS Cost Anomaly Detection findings with every real field (service, region, dates, score, impact, actual/expected spend)
- 🪟 Correctly handles AWS's rolling supported detection window — a selected period only partially covered by AWS's anomaly-detection floor is analyzed for its supported portion and clearly labeled "partial," never silently reported as "no anomalies"
- 🔀 User-selectable Month/Period Comparison, with Gemini's explanation grounded in the exact two periods being compared, across a two-tab **Cost Overview** / **Cost Comparison** layout sharing one date picker
- 🤖 AI cost analysis that never invents a causal story or a resource-level credit attribution AWS doesn't actually support

**Platform**
- 🌐 FastAPI backend exposing both pipelines over HTTP (start/poll investigations, ask follow-up questions, trigger log investigations, refresh cost data, read published dashboard feeds)
- 📉 Streamlit dashboard — Overview, Investigation, Investigation Report, Execution History, Analytics, Cost Explorer, and Settings pages
- 🐳 Docker-based monitoring stack with Prometheus, Grafana, Loki, and Promtail

---


## 4. Architecture Diagram
The AI SRE Agent follows a modular, layered architecture that separates data collection, processing, AI analysis, reporting, and visualization. Each component has a dedicated responsibility, making the system scalable, maintainable, and easy to extend.

<img width="1536" height="1024" alt="ChatGPT Image Jul 31, 2026, 03_52_17 PM" src="https://github.com/user-attachments/assets/f6ba1b9f-c12d-4946-b590-0c74d2d454dc" />

*(The diagram above shows the original infrastructure pipeline; Cost Explorer, described below, runs as a fully separate pipeline alongside it, with its own collector, context builder, prompt builder, and output tree.)*

## 5. Workflow
<img width="1536" height="1024" alt="ChatGPT Image Jul 31, 2026, 03_47_08 PM" src="https://github.com/user-attachments/assets/637308ae-e938-4989-95ad-2babb42ec6fa" />
The workflow illustrates how infrastructure data moves through the platform—from AWS collection to AI-driven analysis and reporting.

### Infrastructure Investigation Workflow

1. **Discover / Collect AWS Infrastructure Data**
   - CloudWatch Metrics (EC2)
   - Application Load Balancer
   - Auto Scaling Groups
   - CloudTrail Events

2. **Store Raw Telemetry**
   - Save collected AWS responses as JSON files inside `output/raw/`.

3. **Build Context**
   - Merge telemetry from multiple AWS services.
   - Normalize infrastructure information.
   - Create per-resource context for AI analysis (EC2 instances, and first-class Load Balancer / Auto Scaling Group resources).

4. **Sanitize & Generate AI Prompt**
   - Strip sensitive network details (DNS names, VPC IDs, private IPs).
   - Attach monitoring context, including metric trend history.
   - Add operational rules and instructions.

5. **AI Analysis**
   - Send prompts to Google Gemini.
   - Perform intelligent Root Cause Analysis (RCA).
   - Generate severity, confidence, root cause, evidence, and recommendations.

6. **Validate Response**
   - Parse JSON response.
   - Handle retries and failures.
   - Validate AI output.

7. **Generate Outputs**
   - AI Reports
   - Execution Summary
   - Logs
   - Historical Archive
   - Dashboard feed (`output/dashboard_feed/`)

8. **Visualize Results**
   - Streamlit Dashboard
   - Incident Reports
   - Execution History
   - Metrics & Trends

Two entry points into this same pipeline are supported: **Full Investigation** (every discovered resource) and **Single Resource Investigation** (one resource, picked from a live AWS discovery call — no dependency on a previous Full Investigation).

### Follow-Up Questions Workflow (optional, on demand)

1. Open a completed report on the **Investigation Report** page and ask a question in the **"Ask AegisOps"** card.
2. The backend re-reads that resource's existing report + context (no new AWS calls), builds a deterministic timeline from the same evidence already collected, and sanitizes it with the existing infrastructure sanitizer.
3. Gemini answers using only that evidence, explicitly labeling observed facts vs. inference vs. hypothesis, and says so plainly if the evidence is insufficient.
4. The question and answer are appended to a persisted, per-investigation conversation (`output/conversations/`) — the original report is never modified.

### Optional Log Investigation Workflow (never automatic)

1. On the same Investigation Report page, click **"🔍 Investigate Logs"** — nothing runs until this is clicked.
2. A bounded incident window is derived from the resource's own metric threshold breaches (falling back to a small, clearly-labeled window when no confident breach exists).
3. The resource's real log source is discovered and fetched for that window only — CloudWatch Logs (EC2), S3 access logs (ALB), or scaling activities (ASG).
4. Raw events are filtered, normalized, deduplicated into patterns, and reduced into a compact evidence package locally — before anything is sent anywhere.
5. A **second, isolated** log sanitizer strips sensitive values (IPs, hostnames, URLs, tokens, credentials) from that package.
6. Gemini analyzes the sanitized package alongside the existing RCA and reports whether it materially changes the original conclusion — shown as a clearly-labeled supplementary result, never overwriting the original report.

### Cost Explorer Workflow

1. **Collect** real AWS Cost Explorer data (total/daily cost, credits, service & region breakdown, cost anomalies) for the current period and the previous equal-length period, plus an optional user-selected comparison period.
2. **Build Context** — derive Gross Cost / Credits / Net Cost at the account, service, and region level.
3. **Sanitize & Prompt** — strip account IDs/ARNs, build a Gemini prompt describing both periods.
4. **AI Analysis** — Gemini explains what changed, which services/regions drove it, and what happened to credits, without ever guessing at data the API doesn't provide (e.g. resource-level credit attribution).
5. **Publish** — a dedicated `output/cost/dashboard_feed/` is written and served through the same FastAPI app, read by the dashboard's Cost Explorer page.

---

## 6. Folder Structure
<img width="1402" height="1122" alt="ChatGPT Image Jul 31, 2026, 04_04_38 PM" src="https://github.com/user-attachments/assets/df0bf48e-3af2-4fae-b30f-b27ab1305a37" />
# 📂 Project Structure

The project is organized into independent modules following a modular architecture. Each module has a dedicated responsibility, improving maintainability, scalability, and future extensibility.


```text
ai-sre-agent/
├── analyzer/          # AI analysis orchestration (infra + Cost Explorer)
├── api/               # FastAPI backend - investigation, follow-up, log investigation & cost-explorer endpoints
├── collector/         # AWS telemetry collectors (CloudWatch, ALB, ASG, CloudTrail, Cost Explorer, optional Logs)
├── config/            # Configuration management
├── context/           # AI context generation (infra + Cost Explorer + Log Evidence Builder)
├── dashboard/         # Streamlit dashboard (pages, components, services)
├── llm/               # Gemini integration (prompt builders + sanitizers, infra + Cost Explorer + Follow-Up + Log Investigation)
├── monitoring/        # Prometheus, Grafana, Loki & Promtail
├── utils/             # Shared utilities (writers, dashboard export, logging, archiving)
├── correlator/        # Correlation engine (currently unused/not wired in)
├── notifier/          # Reserved for future notification services (empty placeholder)
├── sanitizer/          # Reserved top-level placeholder (active sanitizers live under llm/)
├── docs/              # Project documentation
├── main.py            # CLI entry point (infra pipeline, one-shot run)
├── docker-compose.yml # Monitoring stack
├── requirements.txt   # Python dependencies
└── README.md
```
# 🧩 Project Modules

## 📥 Collector

Responsible for collecting infrastructure telemetry and Cost Explorer billing data from AWS.

**Infrastructure modules**

- CloudWatch Metrics (EC2)
- Application Load Balancer
- Auto Scaling Groups
- CloudTrail Events

**Cost Explorer module**

- `collector/cost_explorer.py` — total/daily cost, credit history, service & region breakdown (net *and* credit-only), cost anomalies. Fully separate AWS API surface (`ce` client) and output tree from the infrastructure collectors above.

**Log Investigation module (optional, on demand only)**

- `collector/logs.py` — discovers and fetches a single resource's real log source for a bounded time window: CloudWatch Logs (EC2), S3-delivered access logs (ALB), scaling activities (ASG). Never part of the automatic collector sequence above — invoked only when a user clicks "Investigate Logs".

**Output**

```text
output/raw/            # infrastructure
output/cost/raw/       # Cost Explorer
```

---

## 🧠 Context Builder

Converts raw AWS telemetry into structured AI context.

Responsibilities

- Merge AWS data
- Normalize infrastructure information
- Create per-resource context (EC2, Load Balancer, Auto Scaling Group)
- Prepare AI-ready JSON

A separate `context/cost_context_builder.py` performs the equivalent job for Cost Explorer — deriving Gross Cost / Credits / Net Cost per period, per service, and per region.

`context/log_evidence_builder.py` performs the equivalent reduction job for the optional Log Investigation feature — filters, normalizes, deduplicates, and aggregates raw log events into a compact, bounded "Log Evidence Package" (patterns, timeline, representative samples) entirely locally, before anything is sanitized or sent to Gemini. `utils/incident_window.py` derives the bounded time window it operates on, from the resource's own metric threshold breaches.

**Output**

```text
output/context/
output/cost/context/
```

---

## 🔗 Correlation Engine

`correlator/correlation_engine.py` exists in the codebase but is not currently imported or invoked anywhere in the pipeline — reserved for a future infrastructure-signal correlation feature.

---

## 🤖 Prompt Builder

Generates optimized prompts for Google Gemini.

Responsibilities

- Attach infrastructure or cost context
- Add analysis instructions
- Optimize prompt quality
- Reduce hallucinations
- Explicitly constrain the response schema (e.g. cost drivers must be plain names, not raw data structures; Gemini must never claim data AWS doesn't actually provide)

`llm/prompt_builder.py` (infrastructure), `llm/cost_prompt_builder.py` (Cost Explorer), `llm/follow_up_prompt_builder.py` (Follow-Up Questions), and `llm/log_prompt_builder.py` (Log Investigation) are completely independent prompt builders, each paired with its own sanitizer. Follow-Up reuses the existing infrastructure sanitizer (`llm/sanitizer.py`) unchanged; Log Investigation uses a **brand-new, isolated** `llm/log_sanitizer.py` that operates only on the reduced Log Evidence Package and is never imported into the core RCA pipeline — `llm/sanitizer.py` itself is never modified by either feature.

---

## 🧠 LLM Engine

Communicates with Google Gemini.

Responsibilities

- API communication
- Request timeout (so a hung Gemini call can't block the pipeline indefinitely)
- Retry mechanism (infrastructure pipeline)
- Error handling
- Response validation

Shared by both pipelines — it's a generic prompt-string-in/text-out engine with no knowledge of infrastructure or cost schemas.

---

## 📊 Analyzer

The central orchestration engine.

Responsibilities

- Execute collectors
- Generate prompts
- Invoke Gemini
- Parse responses
- Generate reports

`analyzer/analyzer.py` (infrastructure — supports both Full and Single Resource investigation) and `analyzer/cost_analyzer.py` (Cost Explorer) are separate orchestrators.

Follow-Up Questions and Log Investigation are each orchestrated by their own manager instead (`api/follow_up_manager.py`, `api/log_investigation_manager.py`) — both only ever *read* an existing report/context (never re-run collectors or re-invoke the core Analyzer), so neither can interfere with a Full or Single Resource Investigation in progress.

---

## 📝 Report Writer

Stores AI-generated reports.

**Output**

```text
output/reports/         # one JSON report per infrastructure resource
output/cost/reports/     # one Cost Explorer report per refresh
output/conversations/   # Follow-Up Q&A conversation history, one file per investigation
output/log_investigations/ # Log Investigation results, one file per investigation
```

The original `output/reports/<resource_id>.json` is treated as immutable by both Follow-Up Questions and Log Investigation — neither ever writes to it. Both use the same `investigation_id = "{run_id}__{resource_id}"` scheme and the same staleness check (a resource re-investigated since a report was opened is rejected with a clear message, never silently answered against stale evidence).

---

## 📋 Execution Summary

Generates execution metadata used by dashboards.

Includes

- Execution status
- Reports generated
- Retry count
- Execution duration
- Collector status
- AI model information

**Output**

```text
output/summary/
```

---

## 📦 Archive Manager

Archives every infrastructure investigation execution.

Archive contains

- Raw Data
- Context
- Reports
- Logs
- Summary

**Output**

```text
output/archive/
```

---

## 🌐 API Layer

A FastAPI application (`api/app.py`) exposing both pipelines over HTTP:

- `POST /investigation/full`, `POST /investigation/resource`, `GET /investigation/status/{run_id}` — start and poll infrastructure investigations
- `GET /investigation/resources` — live AWS resource discovery for the Single Resource picker
- `POST /investigation/{investigation_id}/follow-up`, `GET /investigation/{investigation_id}/follow-up` — ask a follow-up question about a completed report, and read back its conversation history
- `POST /investigation/{investigation_id}/logs`, `GET /investigation/{investigation_id}/logs` — trigger the optional Log Investigation stage, and read back its persisted result
- `POST /cost-explorer/refresh` plus `GET /cost-explorer/{summary,history,credits,services,regions,anomalies,comparison,report}` — trigger and read Cost Explorer data
- Read-only passthrough of every published dashboard feed file, so the dashboard can run against a live backend instead of local fixtures
- `DELETE /executions` — clean up execution history

`api/investigation_manager.py`, `api/cost_explorer_manager.py`, `api/follow_up_manager.py`, and `api/log_investigation_manager.py` orchestrate their respective pipelines independently, each with its own lock/state, so a Cost Explorer refresh, a follow-up question, or a log investigation can never block or collide with an infrastructure investigation in progress.

---

## 📉 Dashboard

A Streamlit application (`dashboard/app.py`) with the following pages:

| Page | Purpose |
|------|---------|
| Overview | Account-wide health KPIs and recent activity |
| Investigation | Launch a Full or Single Resource investigation, live progress |
| Investigation Report | View a completed RCA report (severity, root cause, evidence, recommendations, telemetry), ask follow-up questions ("Ask AegisOps"), and optionally trigger a Log Investigation ("🔍 Investigate Logs") |
| Execution History | Past runs, with deletion support |
| Analytics | Trends across executions (incidents, severity, resource distribution) |
| Cost Explorer | Gross/Credits/Net cost, service & region breakdown, credits, anomalies, period comparison, AI cost analysis |
| Settings | Configure the data source (local fixtures, S3, or a live REST backend) |

The dashboard reads everything through a swappable `DataSource` abstraction (local files, S3, or the FastAPI backend above), so it can run standalone against sample data or live against a real deployment.

---

## 📜 Logger

Provides structured logging across the application.

Logs include

- Collector execution
- AI requests
- Errors
- Retry history
- Execution summary

**Output**

```text
output/logs/
```

---

# 🛠️ Technology Stack

| Category | Technology |
|-----------|------------|
| Programming Language | Python 3.11+ |
| Cloud Platform | Amazon Web Services (AWS) |
| AI Model | Google Gemini |
| Backend API | FastAPI + Uvicorn |
| Dashboard | Streamlit + Plotly |
| Monitoring | Amazon CloudWatch, AWS Cost Explorer |
| Log Aggregation | Loki |
| Metrics | Prometheus |
| Visualization | Grafana |
| Containerization | Docker & Docker Compose |
| Version Control | Git & GitHub |

---

# 📁 Output Directory Structure

After every execution, the project generates structured outputs.

```text
output/
│
├── raw/
│   ├── cloudwatch.json
│   ├── alb.json
│   ├── autoscaling.json
│   └── cloudtrail.json
│
├── context/
│   ├── i-xxxxxxxx.json
│   └── ...
│
├── reports/
│   ├── i-xxxxxxxx.json
│   └── ...
│
├── prompts/
│   └── <resource_id>.txt        # persisted Gemini prompts, for debugging
│
├── conversations/
│   └── <run_id>__<resource_id>.json   # Follow-Up Q&A conversation history
│
├── log_investigations/
│   └── <run_id>__<resource_id>.json   # optional Log Investigation results
│
├── logs/
│   └── execution.log
│
├── summary/
│   └── <run_id>.json
│
├── dashboard_feed/
│   └── *.json                   # published feed the dashboard/API read
│
├── archive/
│   └── DD-MM-YYYY_HH-MM-SS/
│
└── cost/                        # Cost Explorer - fully separate tree
    ├── raw/cost_explorer.json
    ├── context/cost_context.json
    ├── reports/cost_report.json
    └── dashboard_feed/*.json
```

### Output Description

| Folder | Description |
|---------|-------------|
| `raw/` | Raw AWS telemetry collected from AWS services |
| `context/` | AI-ready infrastructure context generated from collected data |
| `reports/` | AI-generated Root Cause Analysis reports |
| `prompts/` | Persisted Gemini prompts (debugging aid) |
| `conversations/` | Follow-Up Q&A conversation history, one file per investigation (`run_id__resource_id`) - never overwrites the original report |
| `log_investigations/` | Optional Log Investigation results (evidence package + AI analysis), one file per investigation - only created if "Investigate Logs" was clicked |
| `logs/` | Structured execution logs |
| `summary/` | Dashboard-friendly execution metadata, one file per run |
| `dashboard_feed/` | Published, dashboard-ready JSON, one file per page/concern |
| `archive/` | Historical snapshots of every execution |
| `cost/` | Cost Explorer's entire output tree - raw, context, reports, and dashboard feed - completely independent of everything above |

---

# ⚙️ Prerequisites

Before running the AI SRE Agent, ensure the following software and services are available.

| Requirement | Version |
|------------|---------|
| Python | 3.11 or above |
| Docker | Latest |
| Docker Compose | Latest |
| AWS Account | Active, with Cost Explorer enabled if using that feature |
| Google Gemini API Key | Required |
| Git | Latest |

---

# 📥 Installation

## 1. Clone the Repository

```bash
git clone https://github.com/Mallikarjuningali/ai-sre-agent.git

cd ai-sre-agent
```

---

## 2. Create a Virtual Environment

### Linux / macOS

```bash
python3 -m venv venv

source venv/bin/activate
```

### Windows

```powershell
python -m venv venv

venv\Scripts\activate
```

---

## 3. Install Dependencies

```bash
pip install --upgrade pip

pip install -r requirements.txt
```

---

# 📦 Install Docker Monitoring Stack

The project includes a monitoring stack consisting of:

- Prometheus
- Grafana
- Loki
- Promtail

Start all monitoring services:

```bash
docker compose up -d
```

Verify containers:

```bash
docker ps
```

Expected containers:

```
grafana
prometheus
loki
promtail
```

---

# 🔑 Configuration

## Configure Google Gemini

Create a `.env` file in the project root.

```text
GEMINI_API_KEY=YOUR_GEMINI_API_KEY
```

---

## Configure AWS Credentials

Configure AWS CLI:

```bash
aws configure
```

Provide:

```text
AWS Access Key ID

AWS Secret Access Key

Region

Output Format
```

Example:

```text
AWS Region:
ap-south-1
```

Cost Explorer is only served from `us-east-1` by AWS regardless of your account's default region — the Cost Explorer client is hardcoded to that region so it keeps working even if `REGION` below is changed for other services.

---

## Configure Log Investigation (Optional)

The **Log Investigation** feature ("🔍 Investigate Logs" on the Investigation Report page) is entirely optional and only runs when explicitly clicked — no additional configuration is required for the rest of the platform to work. Nothing below is required unless you want to actually use this feature for a given resource.

### IAM permissions

The credentials configured above need these additional permissions, depending on which resource types you want to investigate logs for:

| Resource | Required IAM actions |
|----------|----------------------|
| EC2 | `logs:DescribeLogGroups`, `logs:DescribeLogStreams`, `logs:FilterLogEvents` |
| Application Load Balancer | `elasticloadbalancing:DescribeLoadBalancers`, `elasticloadbalancing:DescribeLoadBalancerAttributes`, `s3:ListBucket`, `s3:GetObject` (on the bucket ALB access logs are delivered to) |
| Auto Scaling Group | `autoscaling:DescribeScalingActivities` (already required for the normal Auto Scaling collector) |

None of these are required for Full/Single Resource Investigation, Follow-Up Questions, or Cost Explorer — they're only exercised when a log investigation is actually triggered for that specific resource type.

### Fixing "no configured CloudWatch Logs stream was found for this instance" (EC2)

This message means AegisOps looked for, but could not find, a CloudWatch Logs log stream named after the instance's own ID (e.g. `i-0abc123`) in any of the log groups it scanned. This is exactly how the [Unified CloudWatch Agent](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Install-CloudWatch-Agent.html) names log streams by default, so to make an EC2 instance's logs discoverable:

1. **Install the CloudWatch Agent** on the instance, if it isn't already:
   ```bash
   sudo yum install amazon-cloudwatch-agent   # Amazon Linux / RHEL
   # or: sudo apt install amazon-cloudwatch-agent   # Debian/Ubuntu
   ```
2. **Configure it to ship a log file**, with `log_stream_name` set to the instance ID placeholder (this is also the Agent's own default if `log_stream_name` is omitted entirely):
   ```json
   {
     "logs": {
       "logs_collected": {
         "files": {
           "collect_list": [
             {
               "file_path": "/var/log/myapp/application.log",
               "log_group_name": "/myapp/application",
               "log_stream_name": "{instance_id}"
             }
           ]
         }
       }
     }
   }
   ```
   Save this as `/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json` and start/reload the agent:
   ```bash
   sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
     -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json
   ```
3. **Attach an IAM role/policy to the instance** granting it `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents` (the standard `CloudWatchAgentServerPolicy` managed policy covers this) so it can actually deliver logs.
4. If the instance already has many unrelated log groups in the account, discovery is bounded by `LOG_MAX_GROUPS_SCANNED` (`config/settings.py`) — if your instance's log group happens to be scanned after that limit, either raise the limit or keep the relevant log groups easy to find (e.g. a consistent naming prefix).

Once a log group + stream named after the instance ID exists, "Investigate Logs" will find and use it automatically — no code or endpoint changes needed.

### Enabling ALB access logs

By default, ALBs do **not** have access logging enabled — this is a real, off-by-default AWS setting, not an AegisOps limitation. To enable it:

1. In the AWS Console: **EC2 → Load Balancers → (your ALB) → Attributes → Monitoring → Edit** → turn on **Monitor access logs**, and pick/create an S3 bucket.
2. Or via CLI:
   ```bash
   aws elbv2 modify-load-balancer-attributes \
     --load-balancer-arn <your-alb-arn> \
     --attributes Key=access_logs.s3.enabled,Value=true Key=access_logs.s3.bucket,Value=<your-bucket-name>
   ```
3. AWS requires the destination bucket to have a specific bucket policy granting the ELB service account permission to write to it — see [AWS's own access-logging setup guide](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html) for the exact policy for your region.

Until this is enabled, "Investigate Logs" for that ALB will correctly report **"Logs unavailable for this resource"** — never a fabricated "no anomalies" or "no errors found."

### Auto Scaling Groups

No extra configuration is needed — scaling activities (`describe_scaling_activities`) are always queryable for any existing ASG using the same IAM permission the normal Auto Scaling collector already requires.

---

# ⚙️ Configuration File

Project settings are managed from:

```text
config/settings.py
```

Examples:

| Setting | Purpose |
|---------|---------|
| `REGION` | AWS region for EC2/CloudWatch/ALB/ASG |
| `METRIC_LOOKBACK_MINUTES` | CloudWatch/CloudTrail lookback window |
| `METRIC_TREND_LOOKBACK_MINUTES` / `METRIC_TREND_PERIOD_SECONDS` | Metric trend history window fed to Gemini |
| `GEMINI_MODEL` | Gemini model to use |
| `GEMINI_REQUEST_TIMEOUT_SECONDS` | Bounds a single Gemini call so a hung request can't block the pipeline |
| `MAX_RETRIES` / `INITIAL_RETRY_DELAY` / `REQUEST_DELAY` | Retry/backoff behavior for Gemini calls |
| `MAX_RUN_HISTORY` | How many past runs are retained |
| `COST_LOOKBACK_DAYS` | Cost Explorer's default current/previous period length |
| `COST_ANOMALY_MAX_LOOKBACK_DAYS` | Rolling window AWS Cost Anomaly Detection supports - self-corrects from AWS's own error text if it ever drifts |
| `FOLLOW_UP_MAX_CONVERSATION_MESSAGES` / `FOLLOW_UP_PROMPT_HISTORY_MESSAGES` | How much Follow-Up conversation is stored vs. actually sent to Gemini per question |
| `FOLLOW_UP_MAX_QUESTION_LENGTH` | Rejects (rather than silently truncates) an oversized follow-up question |
| `LOG_INCIDENT_WINDOW_BEFORE_MINUTES` / `LOG_INCIDENT_WINDOW_AFTER_MINUTES` | Buffer added around a derived incident window before fetching logs |
| `LOG_FALLBACK_WINDOW_MINUTES` | Bounded window used when no confident incident window can be derived from metrics |
| `LOG_MAX_GROUPS_SCANNED` / `LOG_MAX_S3_OBJECTS_SCANNED` | Bounds on discovering an EC2 CloudWatch Logs stream / listing ALB S3 access-log objects - see [Configure Log Investigation](#configure-log-investigation-optional) below |
| `LOG_MAX_RAW_EVENTS` / `LOG_MAX_RELEVANT_EVENTS` / `LOG_MAX_PATTERNS` | Hard caps on the Log Investigation pipeline's raw event volume, filtered events, and distinct patterns |
| `LOG_MAX_EVIDENCE_PACKAGE_BYTES` | Final size cap on the Log Evidence Package sent to Gemini |

---

# ▶️ Running the Project

## Option A — One-shot CLI run (infrastructure pipeline only)

```bash
python main.py
```

The execution flow is:

```text
Collect AWS Data
  ↓
Generate Raw JSON
  ↓
Build AI Context
  ↓
Generate Prompt
  ↓
Google Gemini Analysis
  ↓
Generate AI Reports
  ↓
Execution Summary
  ↓
Archive Results
  ↓
Completed
```

## Option B — Live API + Dashboard (both pipelines, recommended)

Start the FastAPI backend:

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

In a separate terminal, start the dashboard:

```bash
streamlit run dashboard/app.py
```

Then, from the dashboard's Settings page, point the data source at the running API (or use local fixtures / S3 for read-only browsing without a live backend). From there you can launch Full or Single Resource investigations and Cost Explorer refreshes directly from the UI.

---

# 📊 Expected Output

After a successful execution, the following directory structure is generated.

```text
output/

├── raw/

├── context/

├── reports/

├── prompts/

├── logs/

├── summary/

├── dashboard_feed/

├── archive/

└── cost/
```

---

# 📄 Sample AI Report

Example AI-generated Root Cause Analysis (actual schema returned by Gemini and shown in the Investigation Report page):

```json
{
    "severity": "HIGH",
    "confidence": 92,
    "summary": "Sustained high CPU utilization correlated with an Auto Scaling event.",
    "root_cause": "CPU utilization exceeded 95% for five consecutive minutes, triggering a scale-out that did not fully resolve the load.",
    "evidence": [
        "CPUUtilization stayed above 95% for 5+ minutes in the metric history",
        "An Auto Scaling activity was recorded during the same window"
    ],
    "recommendations": [
        "Scale out further or right-size the instance type",
        "Review application performance under peak load",
        "Investigate long-running processes on the affected instance"
    ]
}
```

---

# 📊 Sample Execution Summary

```json
{
    "status": "COMPLETED",
    "instances_discovered": 5,
    "instances_analyzed": 5,
    "successful": 5,
    "failed": 0,
    "reports_generated": 5,
    "execution_time_seconds": 24.8,
    "retries": 0
}
```

---

# 📝 Logging

Execution logs are automatically generated for every run.

Location:

```text
output/logs/
```

Logs include:

- Collector execution
- Context generation
- AI requests
- Retry attempts
- Errors
- Execution completion

---

# 📦 Archive

Every infrastructure investigation execution is archived automatically.

Each archive contains:

```text
Archive

├── Raw Data

├── Context

├── Reports

├── Logs

└── Summary
```

Archives allow historical comparison of AI analyses and execution history.

---

# 📊 Dashboard

A Streamlit-based dashboard is fully implemented, providing a centralized interface for monitoring infrastructure health, AI-generated incident reports, execution history, cost visibility, and operational insights.

## Implemented

- 📈 Account-wide health overview
- 🤖 AI-generated Root Cause Analysis, per resource
- 🔍 Full and Single Resource investigation launcher with live progress
- ⚡ Live resource discovery ("Refresh Resources") independent of any prior investigation
- 📊 Execution Summary Dashboard
- 📜 Historical Execution History, with deletion
- 📁 Investigation Report viewer
- 💬 Follow-Up Questions ("Ask AegisOps") — evidence-grounded Q&A about a completed report, with persisted conversation history
- 🔍 Optional Log Investigation — on-demand, sanitized log analysis that supplements (never overwrites) the original RCA
- 💳 Cost Explorer — Gross/Credits/Net cost, service & region breakdown, cost anomalies (with correct partial/unsupported date-range handling), period comparison, AI cost analysis
- 🌙 Dark/light theme support

---

# 🛣️ Project Roadmap

## Completed

- AWS Infrastructure Collectors (CloudWatch, ALB, Auto Scaling, CloudTrail)
- Context Builder, Prompt Builder, Google Gemini Integration
- AI Root Cause Analysis with structured JSON reports
- Execution Logging, Retry Mechanism, Execution Summary, Historical Archive
- FastAPI backend for both pipelines
- Full Infrastructure Investigation and Single Resource Investigation
- Live AWS resource discovery for Single Resource Investigation
- Follow-Up Questions ("Ask AegisOps") — evidence-grounded, per-investigation conversation, original RCA immutable
- Optional Log Investigation — bounded incident window, deterministic local reduction pipeline, isolated log sanitizer, supplementary AI analysis
- AWS Cost Explorer collector, context builder, and Gemini cost analysis
- Gross Cost / Credits / Net Cost data model with service & region credit attribution
- AWS Cost Anomaly Detection integration, including correct handling of AWS's rolling supported detection window (partial/unsupported ranges never reported as "no anomalies")
- Streamlit Dashboard (Overview, Investigation, Investigation Report, Execution History, Analytics, Cost Explorer, Settings)
- Docker Monitoring Stack
- GitHub Repository

---

## Future Enhancements

### AI & Automation

- Multi-Agent AI Architecture
- AI Incident Correlation (wiring in `correlator/`)
- Predictive Failure Analysis
- Automated Remediation Suggestions
- AI Chat Assistant

### Cloud Support

- Azure Support
- Google Cloud Platform Support
- Kubernetes Monitoring
- Amazon ECS Monitoring
- Amazon EKS Monitoring
- VPC Flow Logs collector

### Notifications

- Slack Integration
- Microsoft Teams Integration
- Email Notifications
- Webhook Support

### Monitoring

- Live CloudWatch Metrics
- Custom Dashboards
- Grafana Integration
- Alert Correlation
- Trend Analysis

---

# 🤝 Contributing

Contributions are welcome.

If you would like to contribute:

1. Fork the repository
2. Create a feature branch

```bash
git checkout -b feature/new-feature
```

3. Commit your changes

```bash
git commit -m "Add new feature"
```

4. Push your branch

```bash
git push origin feature/new-feature
```

5. Open a Pull Request

---

# 📝 License

This project is licensed under the **MIT License**.

Feel free to use, modify, and distribute this project in accordance with the license.

---

# 👨‍💻 Author

## Mallikarjun Ingali

Cloud Engineer | AWS | DevOps | Python | AI | Site Reliability Engineering

### Connect with Me

- GitHub: https://github.com/Mallikarjuningali
- LinkedIn: https://linkedin.com/in/Mallikarjuningali
- Email: mallikarjuningali809@gmail.com 

---

# ⭐ Support

If you found this project useful:

- ⭐ Star this repository
- 🍴 Fork the repository
- 🛠️ Contribute improvements
- 📢 Share your feedback

---

<div align="center">

## Thank You for Visiting

If you like this project, consider giving it a ⭐ on GitHub.

Made with ❤️ using Python, AWS, Google Gemini, Docker, and Open Source Technologies.

</div>
