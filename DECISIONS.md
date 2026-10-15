# DECISIONS.md

1. **Indexes.** I create a unique index on `employees.emp_code` to prevent concurrent duplicate codes. Department and joined-date indexes support analytics; a unique `(emp_code, date)` index enforces one attendance record per day; `(date, emp_code)` supports attendance filters and sorting. I rejected indexing every field because extra indexes slow writes without helping the main filters.

2. **Punch-in race.** Both requests read the employee and calculate the same attendance date, then insert the same natural key. MongoDB's unique `(emp_code, date)` index allows one insert and rejects the other with `DuplicateKeyError`, returned as 409. There is no unsafe check-then-insert decision.

3. **Ties.** The leaderboard uses MongoDB `$rank`, so equal totals share a rank and the next rank is skipped. Filtering happens after ranking, so a tie at the limit returns every tied employee. Rows then use total minutes and `emp_code` ordering.

4. **Headcount.** The summary starts from employees whose `joined_on` is on or before month-end, then looks up logs. An employee with no logs still contributes one to headcount.

5. **100x data.** I would add pre-aggregated daily/monthly facts and review sharding or archiving after measuring real query plans.
