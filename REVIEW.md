# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `create_employee` | Check-then-insert is not safe for concurrent duplicate `emp_code` requests. | Send two creates for the same code in parallel; both can pass the check. | Added a unique MongoDB index and convert `DuplicateKeyError` to HTTP 409. |
| 2 | `create_employee` | `created_at` used a naive datetime and was returned as a datetime instead of epoch milliseconds. | Inspect the response or run with a non-UTC system timezone. | Store a truncated UTC datetime and serialize it to epoch milliseconds. |
| 3 | `list_employees` | `skip` used `page * page_size`, so page 1 skipped the first page. | Request `page=1`; first employee is missing. | Use `(page - 1) * page_size`. |
| 4 | `list_employees` | `total` ignored the department filter and results had no required sort. | Filter by a department and compare `total`; insert codes out of order. | Count the filtered query and sort by `emp_code` ascending. |
| 5 | `list_employees` | `page` and `page_size` were not validated against the API limits. | Request page 0 or page size 101. | Added FastAPI query validation: page >= 1 and page_size 1-100. |
| 6 | `punch_in` | Unknown employees caused a `None` lookup crash instead of `404`. | Punch in with an unregistered `emp_code`. | Check the employee and return HTTP 404. |
| 7 | `punch_in` | Check-then-insert was vulnerable to duplicate concurrent punch-ins. | Send parallel requests for the same employee/date. | Use the unique `(emp_code, date)` index and handle `DuplicateKeyError` as 409. |
| 8 | `punch_in` | Local naive timestamps produced the wrong date on non-UTC machines and did not truncate milliseconds. | Send an epoch timestamp with milliseconds or run outside UTC. | Normalize through UTC, truncate to seconds, then derive the IST attendance date. |
| 9 | `punch_in` | Overnight shifts used the instant's plain UTC/local date instead of the R1 attendance date. | Punch in at 05:00 IST for a 22:00-06:00 shift. | Apply the previous-day rule for overnight shifts. |
| 10 | `punch_in` | Status was not restricted to `PRESENT`, `WFH`, or `ON_DUTY`. | Send `ABSENT` to punch-in. | Added model validation for presence statuses only. |
| 11 | `punch_in` | Response leaked an internal `id` and returned BSON datetimes instead of epoch milliseconds. | Inspect a successful punch-in response. | Serialize attendance responses at the API boundary and exclude `_id`. |
| 12 | `list_attendance` | Loaded every matching document into Python before sorting and paginating. | Run against 100,000 records; memory and latency grow with the collection. | Apply MongoDB sort, skip, and limit before reading the page. |
| 13 | `list_attendance` | Sorted only by date and did not use the required `emp_code` tie-breaker. | Query two employees on the same date. | Sort by date descending, then emp_code ascending. |
| 14 | `list_attendance` | Date range was not validated, and `date_from > date_to` was accepted. | Send invalid dates or an inverted range. | Validate both dates and return 422 for an inverted range. |
| 15 | `list_attendance` | Returned internal `_id`/BSON datetimes and manually added an undocumented `id`. | Inspect the JSON response. | Use the shared attendance serializer and exclude `_id`. |
| 16 | `list_attendance` | Pagination limits were not enforced. | Request page 0 or page_size 101. | Added FastAPI query validation. |
| 17 | `punch_out` | Missing endpoint meant punch-outs were unsupported. | POST to `/attendance/punch-out` returned 404. | Added atomic punch-out implementation with R3-R5 calculations. |
| 18 | Missing regularization endpoint | Manual corrections and audit history were unsupported. | PATCH to an attendance record returned 404. | Added validation, derived-field recomputation, and history append. |
| 19 | Missing employee monthly analytics | Monthly attendance summary endpoint was unimplemented. | GET `/analytics/employees/{emp_code}/monthly` returned 404. | Added a MongoDB aggregation with joined-on working-day calculation and stored derived values. |
| 20 | Missing department summary | Cross-collection department analytics endpoint was unimplemented. | GET `/analytics/departments/summary` returned 404. | Added an employee-first aggregation so zero-log employees count in headcount and logs are summarized per record. |
| 21 | Missing late leaderboard | Late ranking endpoint was unimplemented. | GET `/analytics/leaderboard/late` returned 404. | Added aggregation with employee join, MongoDB `$rank`, tie-preserving limit, and deterministic ordering. |
| 22 | Missing department trend | Daily gap-filled trend endpoint was unimplemented. | GET `/analytics/departments/{department}/trend` returned 404. | Added a database-generated day series, headcount/log lookups, and a seven-row window average. |
| 23 | Missing explain endpoint | There was no way to verify query plans or index use. | GET `/admin/explain/{endpoint}` returned 404. | Added execution-stats explain responses and endpoint-specific input validation. |

Not defects: MongoDB's internal `_id` is intentionally excluded from employee responses; `emp_code` is the public identity according to the contract.

Also note anything you looked at and decided was **not** a defect, and why.
