"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import calendar
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True)
    db.employees.create_index([("department", ASCENDING), ("emp_code", ASCENDING)])
    db.attendance_logs.create_index(
        [("emp_code", ASCENDING), ("date", ASCENDING)], unique=True
    )
    db.attendance_logs.create_index(
        [("date", DESCENDING), ("emp_code", ASCENDING)]
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(
    punch_in: datetime, shift_start: str, shift_end: Optional[str] = None
) -> int:
    """Apply R1/R2 to an already-normalized instant."""
    punch_in = as_utc(punch_in).astimezone(IST)
    h, m = parse_shift_time(shift_start)
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    # For an overnight shift, a punch-in before shift_end belongs to the
    # shift that started on the previous local calendar day.
    if shift_end and shift_end <= shift_start and punch_in.strftime("%H:%M") < shift_end:
        start -= timedelta(days=1)
    seconds_late = int((punch_in - start).total_seconds())
    if seconds_late <= 10 * 60:
        return 0
    return seconds_late // 60


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    seconds = Decimal(str(int((as_utc(punch_out) - as_utc(punch_in)).total_seconds())))
    hours = (seconds / Decimal("3600")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(hours)


def compute_overtime(
    punch_out: datetime, shift_end: str, date_str: str, shift_start: Optional[str] = None
) -> int:
    h, m = parse_shift_time(shift_end)
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    if shift_start and shift_end <= shift_start:
        end += timedelta(days=1)
    return max(0, int((as_utc(punch_out) - end.astimezone(timezone.utc)).total_seconds()) // 60)


def parse_shift_time(value: str) -> tuple[int, int]:
    h, m = map(int, value.split(":"))
    return h, m


def as_utc(value: datetime) -> datetime:
    """Make PyMongo's naive UTC datetimes safe to compare with aware values."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def truncate_to_second(value: datetime) -> datetime:
    return as_utc(value).replace(microsecond=0)


def epoch_to_datetime(value: Optional[int]) -> datetime:
    if value is None:
        return truncate_to_second(datetime.now(timezone.utc))
    return truncate_to_second(datetime.fromtimestamp(value / 1000, tz=timezone.utc))


def datetime_to_epoch(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    return int(as_utc(value).timestamp() * 1000)


def serialize_employee(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}
    result["created_at"] = datetime_to_epoch(result.get("created_at"))
    return result


def attendance_date_for_punch(punch_in: datetime, employee: dict) -> str:
    local = as_utc(punch_in).astimezone(IST)
    attendance_day = local.date()
    if (
        employee["shift_end"] <= employee["shift_start"]
        and local.strftime("%H:%M") < employee["shift_end"]
    ):
        attendance_day -= timedelta(days=1)
    return attendance_day.isoformat()


def serialize_attendance(doc: dict) -> dict:
    result = {key: value for key, value in doc.items() if key != "_id"}
    result["punch_in"] = datetime_to_epoch(result.get("punch_in"))
    result["punch_out"] = datetime_to_epoch(result.get("punch_out"))
    history = []
    for entry in result.get("history") or []:
        item = dict(entry)
        item["at"] = datetime_to_epoch(item.get("at"))
        changes = {}
        for field, change in (item.get("changes") or {}).items():
            change = dict(change)
            if field in {"punch_in", "punch_out"}:
                change["from"] = datetime_to_epoch(change.get("from"))
                change["to"] = datetime_to_epoch(change.get("to"))
            changes[field] = change
        item["changes"] = changes
        history.append(item)
    result["history"] = history
    return result


def validate_date_string(value: str) -> str:
    date.fromisoformat(value)
    return value


def month_bounds(month: str) -> tuple[str, str]:
    if len(month) != 7 or month[4] != "-":
        raise ValueError("month must be YYYY-MM")
    year, month_number = map(int, month.split("-"))
    if not 1 <= month_number <= 12:
        raise ValueError("month must be YYYY-MM")
    last_day = calendar.monthrange(year, month_number)[1]
    return f"{year:04d}-{month_number:02d}-01", f"{year:04d}-{month_number:02d}-{last_day:02d}"


def round_half_up(value: float, places: int) -> float:
    quantum = Decimal("1").scaleb(-places)
    return float(Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def validate_shift(value: str) -> str:
    if len(value) != 5 or value[2] != ":":
        raise ValueError("must use HH:MM format")
    h, m = parse_shift_time(value)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("must use HH:MM format")
    return value


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    _validate_shift_start = field_validator("shift_start")(validate_shift)
    _validate_shift_end = field_validator("shift_end")(validate_shift)
    _validate_joined_on = field_validator("joined_on")(validate_date_string)

    @model_validator(mode="after")
    def shifts_must_differ(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)
    status: str = Field(default="PRESENT", pattern=r"^(PRESENT|WFH|ON_DUTY)$")


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)


class RegularizeIn(BaseModel):
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)
    punch_out: Optional[int] = Field(default=None, ge=100_000_000_000, le=4_102_444_800_000)
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
# FIX
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = truncate_to_second(datetime.now(timezone.utc))
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return serialize_employee(doc)


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = [
        serialize_employee(doc)
        for doc in db.employees.find(q, {"_id": 0})
        .sort("emp_code", ASCENDING)
        .skip(skip)
        .limit(page_size)
    ]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    ts = epoch_to_datetime(body.punched_at)
    d = attendance_date_for_punch(ts, emp)
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"], emp["shift_end"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
    return serialize_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")

    ts = epoch_to_datetime(body.punched_at)
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$lte": ts}},
        sort=[("punch_in", DESCENDING)],
    )
    if record is None:
        raise HTTPException(404, "no punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "record is already punched out")

    punch_in_at = as_utc(record["punch_in"])
    elapsed_seconds = (ts - punch_in_at).total_seconds()
    if elapsed_seconds <= 0 or elapsed_seconds > 24 * 60 * 60:
        raise HTTPException(422, "punched_at must be after punch_in and within 24 hours")

    work_hours = compute_work_hours(punch_in_at, ts)
    update = {
        "punch_out": ts,
        "work_hours": work_hours,
        "overtime_minutes": compute_overtime(
            ts, emp["shift_end"], record["date"], emp["shift_start"]
        ),
        "half_day": work_hours < 4.50,
    }
    updated = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None},
        {"$set": update},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "record is already punched out")
    return serialize_attendance(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    try:
        validate_date_string(date)
    except ValueError:
        raise HTTPException(422, "date must be YYYY-MM-DD")

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(404, "employee not found")
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "attendance record not found")

    old_status = record.get("status")
    old_punch_in = record.get("punch_in")
    old_punch_out = record.get("punch_out")
    old_derived = {
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }
    final_status = body.status if body.status is not None else old_status

    if final_status in {"ABSENT", "LEAVE"}:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "ABSENT or LEAVE cannot have punch times")
        final_punch_in = None
        final_punch_out = None
    else:
        final_punch_in = epoch_to_datetime(body.punch_in) if body.punch_in is not None else old_punch_in
        final_punch_out = epoch_to_datetime(body.punch_out) if body.punch_out is not None else old_punch_out
        if final_punch_in is None:
            raise HTTPException(422, "presence status requires punch_in")
        if attendance_date_for_punch(final_punch_in, emp) != date:
            raise HTTPException(422, "punch_in does not belong to the record date")
        if final_punch_out is not None:
            elapsed = (as_utc(final_punch_out) - as_utc(final_punch_in)).total_seconds()
            if elapsed <= 0 or elapsed > 24 * 60 * 60:
                raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")

    if final_punch_in is None:
        derived = {"work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False}
    else:
        late_minutes = compute_late_minutes(
            final_punch_in, emp["shift_start"], emp["shift_end"]
        )
        if final_punch_out is None:
            derived = {"work_hours": None, "late_minutes": late_minutes, "overtime_minutes": 0, "half_day": False}
        else:
            work_hours = compute_work_hours(final_punch_in, final_punch_out)
            derived = {
                "work_hours": work_hours,
                "late_minutes": late_minutes,
                "overtime_minutes": compute_overtime(
                    final_punch_out, emp["shift_end"], date, emp["shift_start"]
                ),
                "half_day": work_hours < 4.50,
            }

    new_values = {
        "status": final_status,
        "punch_in": final_punch_in,
        "punch_out": final_punch_out,
        **derived,
    }
    old_values = {
        "status": old_status,
        "punch_in": old_punch_in,
        "punch_out": old_punch_out,
        **old_derived,
    }
    changes = {}
    for field in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"):
        before, after = old_values[field], new_values[field]
        if field in {"punch_in", "punch_out"}:
            equal = (before is None and after is None) or (
                before is not None and after is not None and as_utc(before) == as_utc(after)
            )
        else:
            equal = before == after
        if not equal:
            changes[field] = {"from": before, "to": after}
    if not changes:
        raise HTTPException(422, "request changes nothing")

    history = record.get("history") or []
    history_entry = {
        "at": truncate_to_second(datetime.now(timezone.utc)),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }
    filter_query = {
        "_id": record["_id"],
        "$or": [{"history": history}, {"history": {"$exists": False}}],
    }
    updated = db.attendance_logs.find_one_and_update(
        filter_query,
        {"$set": new_values, "$push": {"history": history_entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "attendance record changed concurrently")
    return serialize_attendance(updated)


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    try:
        month_start, month_end = month_bounds(month)
    except (ValueError, TypeError):
        raise HTTPException(422, "month must be YYYY-MM")

    pipeline = [
        {"$match": {"emp_code": emp_code}},
        {"$set": {
            "join_date": {"$dateFromString": {"dateString": "$joined_on"}},
            "period_start": {"$dateFromString": {"dateString": month_start}},
            "period_end": {"$dateFromString": {"dateString": month_end}},
        }},
        {"$set": {
            "working_start": {"$cond": [
                {"$gt": ["$join_date", "$period_start"]}, "$join_date", "$period_start"
            ]}
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"employee": "$emp_code"},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$emp_code", "$$employee"]},
                    {"$gte": ["$date", month_start]},
                    {"$lte": ["$date", month_end]},
                ]}}}
            ],
            "as": "logs",
        }},
        {"$set": {
            "working_days": {"$size": {"$filter": {
                "input": {"$map": {
                    "input": {"$range": [
                        0,
                        {"$add": [{"$dateDiff": {
                            "startDate": "$working_start", "endDate": "$period_end", "unit": "day"
                        }}, 1]}
                    ]},
                    "as": "offset",
                    "in": {"$dateAdd": {
                        "startDate": "$working_start", "unit": "day", "amount": "$$offset"
                    }},
                }},
                "as": "day",
                "cond": {"$and": [
                    {"$gte": [{"$dayOfWeek": "$$day"}, 2]},
                    {"$lte": [{"$dayOfWeek": "$$day"}, 6]},
                ]},
            }}}
        }},
        {"$set": {
            "stats": {"$reduce": {
                "input": "$logs",
                "initialValue": {
                    "present_days": 0,
                    "leave_days": 0,
                    "late_count": 0,
                    "total_late_minutes": 0,
                    "total_overtime_minutes": 0,
                },
                "in": {
                    "present_days": {"$add": ["$$value.present_days", {"$cond": [
                        {"$and": [
                            {"$in": ["$$this.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$$this.date"}}}, 2]},
                            {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$$this.date"}}}, 6]},
                        ]},
                        {"$cond": [{"$ifNull": ["$$this.half_day", False]}, 0.5, 1]}, 0
                    ]}]},
                    "leave_days": {"$add": ["$$value.leave_days", {"$cond": [{"$eq": ["$$this.status", "LEAVE"]}, 1, 0]}]},
                    "late_count": {"$add": ["$$value.late_count", {"$cond": [{"$gt": [{"$ifNull": ["$$this.late_minutes", 0]}, 0]}, 1, 0]}]},
                    "total_late_minutes": {"$add": ["$$value.total_late_minutes", {"$ifNull": ["$$this.late_minutes", 0]}]},
                    "total_overtime_minutes": {"$add": ["$$value.total_overtime_minutes", {"$ifNull": ["$$this.overtime_minutes", 0]}]},
                },
            }}
        }},
        {"$project": {"_id": 0, "emp_code": 1, "working_days": 1, "stats": 1}},
    ]
    result = next(db.employees.aggregate(pipeline), None)
    if result is None:
        raise HTTPException(404, "employee not found")
    stats = result["stats"]
    working_days = result["working_days"]
    attendance_pct = None if working_days == 0 else round_half_up(
        stats["present_days"] / working_days * 100, 2
    )
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": stats["present_days"],
        "leave_days": stats["leave_days"],
        "late_count": stats["late_count"],
        "total_late_minutes": stats["total_late_minutes"],
        "total_overtime_minutes": stats["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            try:
                validate_date_string(date_from)
            except ValueError:
                raise HTTPException(422, "date_from must be YYYY-MM-DD")
            q["date"]["$gte"] = date_from
        if date_to:
            try:
                validate_date_string(date_to)
            except ValueError:
                raise HTTPException(422, "date_to must be YYYY-MM-DD")
            q["date"]["$lte"] = date_to
        if date_from and date_to and date_from > date_to:
            raise HTTPException(422, "date_from must not be after date_to")
    if status:
        q["status"] = status
    total = db.attendance_logs.count_documents(q)
    page_docs = db.attendance_logs.find(q, {"_id": 0}).sort(
        [("date", DESCENDING), ("emp_code", ASCENDING)]
    ).skip((page - 1) * page_size).limit(page_size)
    items = [serialize_attendance(doc) for doc in page_docs]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
