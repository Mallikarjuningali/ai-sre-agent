<div align="center">

# 🤖 AI SRE Agent

### AI-Powered Site Reliability Engineering Platform for AWS Infrastructure

An intelligent observability platform that automatically collects AWS infrastructure telemetry, performs AI-driven Root Cause Analysis (RCA) using Google Gemini, and generates actionable insights for cloud operations teams.

---

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![AWS](https://img.shields.io/badge/AWS-Cloud-FF9900?style=for-the-badge&logo=amazonaws&logoColor=white)
![Google Gemini](https://img.shields.io/badge/Google-Gemini-4285F4?style=for-the-badge&logo=google&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Containerized-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-Monitoring-F46800?style=for-the-badge&logo=grafana&logoColor=white)
![Prometheus](https://img.shields.io/badge/Prometheus-Metrics-E6522C?style=for-the-badge&logo=prometheus&logoColor=white)

</div>

---

## 📖 Overview

AI SRE Agent is an intelligent cloud observability and incident analysis platform built for AWS environments. It continuously collects infrastructure telemetry from AWS services, transforms the collected data into structured AI context, and leverages Google Gemini to perform automated Root Cause Analysis (RCA).

Instead of manually reviewing CloudWatch metrics, Auto Scaling events, Application Load Balancer health, and CloudTrail logs, the AI SRE Agent correlates infrastructure signals and generates detailed, human-readable incident reports with severity classification, probable root causes, operational impact, and remediation recommendations.

The project follows a modular architecture, making it easy to extend with additional AWS services, monitoring sources, notification channels, or AI models.

---

## 🚀 Key Features

- 📊 Collect infrastructure telemetry from AWS services
- 📈 Analyze CloudWatch metrics and alarms
- ⚖️ Monitor Application Load Balancer health
- 🔄 Inspect Auto Scaling Group activity
- 📜 Process CloudTrail events
- 🧠 AI-powered Root Cause Analysis using Google Gemini
- 📄 Generate structured JSON incident reports
- 📝 Execution logging with retry mechanism
- 📦 Automatic archival of execution history
- 📊 Execution summary generation for dashboards
- 📁 Organized raw, context, report, log, and archive storage
- 🐳 Docker-based monitoring stack with Prometheus, Grafana, Loki, and Promtail
- 📉 Streamlit Dashboard (Coming Soon)

---


## 4. Architecture Diagram
The AI SRE Agent follows a modular, layered architecture that separates data collection, processing, AI analysis, reporting, and visualization. Each component has a dedicated responsibility, making the system scalable, maintainable, and easy to extend.

<img width="1536" height="1024" alt="ChatGPT Image Jul 31, 2026, 03_52_17 PM" src="https://github.com/user-attachments/assets/f6ba1b9f-c12d-4946-b590-0c74d2d454dc" />

## 5. Workflow
<img width="1536" height="1024" alt="ChatGPT Image Jul 31, 2026, 03_47_08 PM" src="https://github.com/user-attachments/assets/637308ae-e938-4989-95ad-2babb42ec6fa" />
The workflow illustrates how infrastructure data moves through the platform—from AWS collection to AI-driven analysis and reporting.

### Workflow Steps

1. **Collect AWS Infrastructure Data**
   - CloudWatch Metrics
   - Application Load Balancer
   - Auto Scaling Groups
   - CloudTrail Events
   - VPC Flow Logs (Optional)

2. **Store Raw Telemetry**
   - Save collected AWS responses as JSON files inside `output/raw/`.

3. **Build Context**
   - Merge telemetry from multiple AWS services.
   - Normalize infrastructure information.
   - Create instance-wise context for AI analysis.

4. **Generate AI Prompt**
   - Construct structured prompts.
   - Attach monitoring context.
   - Include operational rules and instructions.

5. **AI Analysis**
   - Send prompts to Google Gemini.
   - Perform intelligent Root Cause Analysis (RCA).
   - Generate severity, impact, and recommendations.

6. **Validate Response**
   - Parse JSON response.
   - Handle retries and failures.
   - Validate AI output.

7. **Generate Outputs**
   - AI Reports
   - Execution Summary
   - Logs
   - Historical Archive

8. **Visualize Results**
   - Streamlit Dashboard (Upcoming)
   - Incident Reports
   - Execution History
   - Metrics & Trends

---

## 6. Folder Structure
<img width="1402" height="1122" alt="ChatGPT Image Jul 31, 2026, 04_04_38 PM" src="https://github.com/user-attachments/assets/df0bf48e-3af2-4fae-b30f-b27ab1305a37" />
# 📂 Project Structure

The project is organized into independent modules following a modular architecture. Each module has a dedicated responsibility, improving maintainability, scalability, and future extensibility.


```text
ai-sre-agent/
├── analyzer/          # AI analysis orchestration
├── collector/         # AWS telemetry collectors
├── config/            # Configuration management
├── context/           # AI context generation
├── correlator/        # Correlation engine
├── llm/               # Gemini integration
├── monitoring/        # Prometheus, Grafana, Loki & Promtail
├── notifier/          # Future notification services
├── sanitizer/         # Data sanitization
├── utils/             # Shared utilities
├── docs/              # Project documentation
├── main.py            # Application entry point
├── docker-compose.yml # Monitoring stack
├── requirements.txt   # Python dependencies
└── README.md
```
# 🧩 Project Modules

## 📥 Collector

Responsible for collecting infrastructure telemetry from AWS.

**Modules**

- CloudWatch Metrics
- Application Load Balancer
- Auto Scaling Groups
- CloudTrail Events
- VPC Flow Logs

**Output**

```text
output/raw/
```

---

## 🧠 Context Builder

Converts raw AWS telemetry into structured AI context.

Responsibilities

- Merge AWS data
- Normalize infrastructure information
- Create instance-wise context
- Prepare AI-ready JSON

**Output**

```text
output/context/
```

---

## 🔗 Correlation Engine

Correlates infrastructure signals collected from different AWS services.

Responsibilities

- Match EC2 instances
- Combine infrastructure events
- Detect relationships
- Build unified operational context

---

## 🤖 Prompt Builder

Generates optimized prompts for Google Gemini.

Responsibilities

- Attach infrastructure context
- Add analysis instructions
- Optimize prompt quality
- Reduce hallucinations

---

## 🧠 LLM Engine

Communicates with Google Gemini.

Responsibilities

- API communication
- Retry mechanism
- Error handling
- Response validation

---

## 📊 Analyzer

The central orchestration engine.

Responsibilities

- Execute collectors
- Generate prompts
- Invoke Gemini
- Parse responses
- Generate reports

---

## 📝 Report Writer

Stores AI-generated Root Cause Analysis reports.

**Output**

```text
output/reports/
```

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

Archives every execution.

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
| Monitoring | Amazon CloudWatch |
| Log Aggregation | Loki |
| Metrics | Prometheus |
| Visualization | Grafana |
| Dashboard | Streamlit (Upcoming) |
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
├── logs/
│   └── execution.log
│
├── summary/
│   └── execution-summary.json
│
└── archive/
    └── DD-MM-YYYY_HH-MM-SS/
```

### Output Description

| Folder | Description |
|---------|-------------|
| `raw/` | Stores raw AWS telemetry collected from AWS services |
| `context/` | AI-ready infrastructure context generated from collected data |
| `reports/` | AI-generated Root Cause Analysis reports |
| `logs/` | Structured execution logs |
| `summary/` | Dashboard-friendly execution metadata |
| `archive/` | Historical snapshots of every execution |

---

# ⚙️ Prerequisites

Before running the AI SRE Agent, ensure the following software and services are available.

| Requirement | Version |
|------------|---------|
| Python | 3.11 or above |
| Docker | Latest |
| Docker Compose | Latest |
| AWS Account | Active |
| Google Gemini API Key | Required |
| Git | Latest |

---

# 📥 Installation

## 1. Clone the Repository

```bash
git clone https://github.com/<your-github-username>/ai-sre-agent.git

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

---

# ⚙️ Configuration File

Project settings are managed from:

```text
config/settings.py
```

Examples:

- AWS Region
- Retry Count
- Retry Delay
- Gemini Model
- Request Delay
- Archive History Limit
- Output Paths

---

# ▶️ Running the Project

Run the AI SRE Agent:

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

---

# 📊 Expected Output

After a successful execution, the following directory structure is generated.

```text
output/

├── raw/

├── context/

├── reports/

├── logs/

├── summary/

└── archive/
```

---

# 📄 Sample AI Report

Example AI-generated Root Cause Analysis:

```json
{
    "severity": "High",
    "root_cause": "CPU utilization exceeded 95% for five consecutive minutes.",
    "impact": "Application response time increased significantly.",
    "recommendation": [
        "Scale out EC2 instances.",
        "Review application performance.",
        "Investigate long-running processes."
    ]
}
```

---

# 📊 Sample Execution Summary

```json
{
    "status": "SUCCESS",
    "instances_discovered": 5,
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

Every execution is archived automatically.

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

# 📊 Dashboard (Coming Soon)

A web-based dashboard is currently under development using **Streamlit**.

The dashboard will provide a centralized interface for monitoring infrastructure health, AI-generated incident reports, execution history, and operational insights.

## Planned Features

- 📈 Real-time infrastructure health
- 🤖 AI-generated Root Cause Analysis
- 📊 Execution Summary Dashboard
- 📜 Historical Execution History
- 📁 AI Report Explorer
- 🔍 Search & Filter Reports
- 📉 Infrastructure Metrics Visualization
- 🔄 Auto Refresh
- 🌙 Dark Mode

---

# 🛣️ Project Roadmap

## Completed

- AWS Infrastructure Collectors
- CloudWatch Integration
- Application Load Balancer Collector
- Auto Scaling Group Collector
- CloudTrail Collector
- Context Builder
- Prompt Builder
- Google Gemini Integration
- AI Root Cause Analysis
- Structured JSON Reports
- Execution Logging
- Retry Mechanism
- Execution Summary
- Historical Archive
- Docker Monitoring Stack
- GitHub Repository

---

## In Progress

- Streamlit Dashboard
- Interactive Report Viewer
- Execution History Visualization

---

## Future Enhancements

### AI & Automation

- Multi-Agent AI Architecture
- AI Incident Correlation
- Predictive Failure Analysis
- Automated Remediation Suggestions
- AI Chat Assistant

### Cloud Support

- Azure Support
- Google Cloud Platform Support
- Kubernetes Monitoring
- Amazon ECS Monitoring
- Amazon EKS Monitoring

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
