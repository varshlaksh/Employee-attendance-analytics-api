# DECISIONS.md

1. **Indexes.** I create a unique index on `employees.emp_code` so duplicate employee codes cannot be written concurrently. I index employee department and joined date for department analytics, a unique index on `(emp_code, date)` for one attendance record per day, and `(date, emp_code)` for filtered attendance listing and its sort. I considered indexing every analytics field, but rejected that because extra indexes slow writes and increase storage without helping the main filters.

2. **Punch-in race.** Both requests first read the employee and calculate the same attendance date. They then try to insert the same natural key. MongoDB's unique `(emp_code, date)` index allows one insert and rejects the other with `DuplicateKeyError`, which the API returns as 409. There is no unsafe check-then-insert decision.

3. **Ties.** The leaderboard uses MongoDB `$rank`, so equal totals share a rank and the next rank is skipped. Filtering happens after ranking, so a tie at the limit returns every tied employee. Rows are then ordered by total minutes and `emp_code`.

4. **Headcount.** The summary starts from employees whose `joined_on` is on or before month-end, then looks up logs. Therefore an employee with no logs still contributes one to headcount.

5. **100x data.** I would add pre-aggregated daily/monthly facts and review shard or archive strategy after measuring real query plans.
