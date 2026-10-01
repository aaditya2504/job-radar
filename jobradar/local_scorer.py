"""Free, offline scoring: compare skills in the resume against skills in the posting.

No API calls and no cost. Deterministic, so the same posting always gets the same score.
"""
import re

# canonical skill -> regex aliases (matched case-insensitively on word boundaries)
SKILLS = {
    "Python": [r"python"], "SQL": [r"sql"], "Java": [r"java"], "Scala": [r"scala"],
    "Spark": [r"spark", r"pyspark"], "Kafka": [r"kafka"], "Flink": [r"flink"],
    "Airflow": [r"airflow"], "dbt": [r"dbt"], "Hadoop": [r"hadoop"], "Hive": [r"hive"],
    "Snowflake": [r"snowflake"], "Databricks": [r"databricks"], "BigQuery": [r"bigquery"],
    "Redshift": [r"redshift"], "Iceberg": [r"iceberg"], "Trino": [r"trino", r"presto"],
    "PostgreSQL": [r"postgres", r"postgresql"], "MySQL": [r"mysql"], "Oracle": [r"oracle"],
    "Teradata": [r"teradata"], "MongoDB": [r"mongodb", r"mongo"],
    "AWS": [r"aws", r"amazon web services"], "S3": [r"s3"], "Glue": [r"aws glue", r"glue"],
    "Athena": [r"athena"], "Lambda": [r"lambda"], "Azure": [r"azure"],
    "GCP": [r"gcp", r"google cloud"], "Docker": [r"docker"],
    "Kubernetes": [r"kubernetes", r"k8s"], "Terraform": [r"terraform"], "Git": [r"git", r"github"],
    "Linux": [r"linux", r"unix"], "ETL": [r"etl", r"elt"],
    "CDC": [r"cdc", r"change data capture"], "Debezium": [r"debezium"],
    "Data modeling": [r"data model(?:l)?ing", r"dimensional model(?:l)?ing", r"star schema"],
    "Streaming": [r"streaming", r"real-time", r"real time"],
    "Tableau": [r"tableau"], "Power BI": [r"power bi"],
    "pandas": [r"pandas"], "NumPy": [r"numpy"], "scikit-learn": [r"scikit-learn", r"sklearn"],
    "Machine learning": [r"machine learning"],
}
_PATTERNS = {name: re.compile(r"\b(?:" + "|".join(a) + r")\b", re.I) for name, a in SKILLS.items()}

CORE_TITLES = ["data engineer", "data platform", "data pipeline", "data infrastructure",
               "etl", "analytics engineer", "analytics engineering"]
ADJACENT_TITLES = ["data analyst", "data science", "data scientist", "machine learning",
                   "software engineer", "business intelligence"]

TITLE_POINTS = {"core": 40, "adjacent": 20, "other": 10}
SKILL_POINTS = 60
TITLE_ONLY_SKILL_POINTS = 30  # neutral credit when a posting has no description to compare


def extract_skills(text):
    return {name for name, pat in _PATTERNS.items() if pat.search(text or "")}


def title_tier(title):
    t = (title or "").lower()
    if any(k in t for k in CORE_TITLES):
        return "core"
    if any(k in t for k in ADJACENT_TITLES):
        return "adjacent"
    return "other"


def score_local(job, resume_skills):
    """Return (score 0-100, reasons) for a posting, given the skills found in the resume."""
    tier = title_tier(job.title)
    score = TITLE_POINTS[tier]
    reasons = [{"core": "Core data engineering title", "adjacent": "Adjacent data role",
                "other": "Title is a weak match"}[tier]]

    wanted = extract_skills(f"{job.title} {job.description}")
    if job.description and wanted:
        matched = sorted(wanted & resume_skills)
        gaps = sorted(wanted - resume_skills)
        score += round(SKILL_POINTS * len(matched) / len(wanted))
        reasons.append(f"Matches {len(matched)}/{len(wanted)} posting skills"
                       + (f": {', '.join(matched[:5])}" if matched else ""))
        if gaps:
            reasons.append(f"Gaps: {', '.join(gaps[:4])}")
    else:
        score += TITLE_ONLY_SKILL_POINTS
        reasons.append("No description available: title-based score")
    return min(score, 100), reasons
