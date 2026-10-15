# Employee Attendance Analytics API

FastAPI and MongoDB service for employee attendance, punch events, audit corrections, and analytics.

## Run locally

Requirements: Python 3.11+, MongoDB 6.0+ (the evaluator uses MongoDB 7).

PowerShell:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

Set `MONGO_URI` and `MONGO_DB` in `.env` or in the environment. For local MongoDB with Docker:

```powershell
docker run -d --name attendance-mongo -p 27017:27017 mongo:7
```

Start the API from the repository root:

```powershell
python -m uvicorn app.main:app --port 8000
```

Readiness check:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Implementation notes

- All instants cross the API boundary as epoch milliseconds and are stored as UTC BSON dates.
- Attendance dates and shifts follow IST, including overnight shifts.
- Duplicate employee and attendance natural keys are protected by unique MongoDB indexes.
- Analytics use MongoDB aggregation pipelines and stored derived values.
- `REVIEW.md` records starter defects and their fixes; `DECISIONS.md` explains the main design choices.

## Local verification

```powershell
.\\.venv\\Scripts\\python.exe -m py_compile app/main.py
```

The full evaluator dataset is not included in this repository. Edge cases such as overnight shifts, missing logs, ties, weekends, and mid-month joiners should be tested against a MongoDB dataset before submission.
