# Offline mock-LLM end-to-end run

- user: `e2e-mock-player`
- Dify: `http://127.0.0.1:8080`

## Turn 1

Player: I promise to return the golden compass to you before midnight.

Inspector Recall (368 ms):

Inspector Recall: The casebook is empty, so this is your first testimony. I have written it down, suspect - and I will hold you to every word. Tell me, who else saw you that night?

## Turn 2

Player: Honestly, I never promised you anything about a compass.

Inspector Recall (383 ms):

Casebook from PowerContext:
- [observation] Player testimony: I promise to return the golden compass to you before midnight.

Inspector Recall: The casebook remembers you, suspect. You swore: “I promise to return the golden compass to you before midnight.” — and tonight your story changed. I have the record in front of me, so do not bother denying it. Why should I believe this new version?

## Turn 3

Player: What did I promise you before midnight?

Inspector Recall (383 ms):

Casebook from PowerContext:
- Player testimony: I promise to return the golden compass to you before midnight.
- Player testimony: Honestly, I never promised you anything about a compass.

Inspector Recall: The casebook remembers you, suspect. You swore: “I promise to return the golden compass to you before midnight.” — and tonight your story changed. I have the record in front of me, so do not bother denying it. Why should I believe this new version?

## Node execution trace

### run `d8170cfe-67f6-40b3-a7a1-7d60c480b48b` - succeeded

| node | type | status | ms |
|---|---|---|---|
| Start | start | succeeded | 0.1 |
| Prepare recall request | code | succeeded | 67.8 |
| Recall PowerContext casebook | http-request | succeeded | 48.3 |
| Parse recalled casebook | code | succeeded | 47.9 |
| Inspector Recall NPC | llm | succeeded | 12.6 |
| Format durable memory | code | succeeded | 48.6 |
| Save Inspector case note | http-request | succeeded | 31.8 |
| Inspector reply | answer | succeeded | 0.1 |

### run `f1b6be65-eb25-4a6c-bcce-54c4cdefc633` - succeeded

| node | type | status | ms |
|---|---|---|---|
| Start | start | succeeded | 0.1 |
| Prepare recall request | code | succeeded | 54.5 |
| Recall PowerContext casebook | http-request | succeeded | 81.0 |
| Parse recalled casebook | code | succeeded | 45.6 |
| Inspector Recall NPC | llm | succeeded | 10.1 |
| Format durable memory | code | succeeded | 50.1 |
| Save Inspector case note | http-request | succeeded | 34.2 |
| Inspector reply | answer | succeeded | 0.1 |

### run `e604f8e4-e03f-48b9-a9b1-73df5d7b105c` - succeeded

| node | type | status | ms |
|---|---|---|---|
| Start | start | succeeded | 0.1 |
| Prepare recall request | code | succeeded | 61.4 |
| Recall PowerContext casebook | http-request | succeeded | 52.1 |
| Parse recalled casebook | code | succeeded | 39.1 |
| Inspector Recall NPC | llm | succeeded | 10.5 |
| Format durable memory | code | succeeded | 38.8 |
| Save Inspector case note | http-request | succeeded | 25.6 |
| Inspector reply | answer | succeeded | 0.1 |
