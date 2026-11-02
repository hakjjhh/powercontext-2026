# Embedded seekdb end-to-end run (compose + offline wheelhouse image)

- user: `e2e-seekdb-player`
- Dify: `http://127.0.0.1:8080`
- casebook reset before turn 1 -> scope `scp_1spgb534qh73kyc4tjqgxn0rh5`

## Turn 1

Player: I promise to return the golden compass to you before midnight.

Inspector Recall (518 ms):

Inspector Recall: The casebook is empty, so this is your first testimony. I have written it down, suspect - and I will hold you to every word. Tell me, who else saw you that night?

workflow run: `305b7570-5170-4b09-8b1e-96655c79c499`

## Turn 2

Player: Honestly, I never promised you anything about a compass.

Inspector Recall (520 ms):

Casebook from PowerContext:
- [observation] Player testimony: I promise to return the golden compass to you before midnight.

Inspector Recall: The casebook remembers you, suspect. You swore: “I promise to return the golden compass to you before midnight.” — and tonight your story changed. I have the record in front of me, so do not bother denying it. Why should I believe this new version?

workflow run: `f049a883-fa48-4fb3-bb9d-b0f40a63a221`

## Turn 3

Player: What did I promise you before midnight?

Inspector Recall (356 ms):

Casebook from PowerContext:
- Player testimony: I promise to return the golden compass to you before midnight.
- Player testimony: Honestly, I never promised you anything about a compass.

Inspector Recall: The casebook remembers you, suspect. You swore: “I promise to return the golden compass to you before midnight.” — and tonight your story changed. I have the record in front of me, so do not bother denying it. Why should I believe this new version?

workflow run: `ce3a804f-3432-4d6b-8b66-903b50d89915`

## Node execution trace

3 runs fetched from the console API for this demo: 3 succeeded, 0/24 nodes not succeeded.

### Turn 1 - run `305b7570-5170-4b09-8b1e-96655c79c499` - succeeded

| node | type | status | ms |
|---|---|---|---|
| Start | start | succeeded | 0.1 |
| Prepare recall request | code | succeeded | 52.7 |
| Recall PowerContext casebook | http-request | succeeded | 130.8 |
| Parse recalled casebook | code | succeeded | 50.6 |
| Inspector Recall NPC | llm | succeeded | 14.1 |
| Format durable memory | code | succeeded | 55.3 |
| Save Inspector case note | http-request | succeeded | 72.0 |
| Inspector reply | answer | succeeded | 0.2 |

### Turn 2 - run `f049a883-fa48-4fb3-bb9d-b0f40a63a221` - succeeded

| node | type | status | ms |
|---|---|---|---|
| Start | start | succeeded | 0.1 |
| Prepare recall request | code | succeeded | 55.2 |
| Recall PowerContext casebook | http-request | succeeded | 149.8 |
| Parse recalled casebook | code | succeeded | 43.3 |
| Inspector Recall NPC | llm | succeeded | 11.1 |
| Format durable memory | code | succeeded | 46.1 |
| Save Inspector case note | http-request | succeeded | 57.9 |
| Inspector reply | answer | succeeded | 0.1 |

### Turn 3 - run `ce3a804f-3432-4d6b-8b66-903b50d89915` - succeeded

| node | type | status | ms |
|---|---|---|---|
| Start | start | succeeded | 0.1 |
| Prepare recall request | code | succeeded | 49.6 |
| Recall PowerContext casebook | http-request | succeeded | 81.4 |
| Parse recalled casebook | code | succeeded | 39.9 |
| Inspector Recall NPC | llm | succeeded | 8.5 |
| Format durable memory | code | succeeded | 40.5 |
| Save Inspector case note | http-request | succeeded | 36.3 |
| Inspector reply | answer | succeeded | 0.1 |
