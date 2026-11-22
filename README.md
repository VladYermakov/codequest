# CodeQuest classroom

A small Python practice site with browser-based Python execution and a shared classroom leaderboard.

## Run locally

```sh
cd outputs
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TEACHER_USERNAME=teacher
export TEACHER_PASSWORD='choose-a-private-password-of-at-least-12-characters'
python app.py
```

Open http://localhost:5000. The app uses a local SQLite database by default. Students create a username and password or sign in; passwords are stored as hashes, and the server takes the leaderboard identity from the signed-in session. Completed challenge IDs are stored on the server per account and class, and merged with browser progress when a student signs in. Editor drafts are remembered in that browser per profile, class, and task. Each student's latest run for a task (code, output, and whether it matched the expected answer) is saved on the server for the teacher at `/teacher`. School grades are visible only in that teacher panel, never on student pages or the leaderboard. Pyodide is loaded from jsDelivr, so students need an internet connection for the first Python runtime download.

## Use Neon Postgres locally or on Render

The Neon CLI writes the pooled `DATABASE_URL` into `.env.local` after `neon deploy`. `.env.local` is ignored by Git, and the app loads it automatically for local development. SQLAlchemy connects with psycopg 3 and checks pooled connections before reusing them. Keep `DATABASE_URL_UNPOOLED` private and use the pooled `DATABASE_URL` for this web app.

For Render, add `DATABASE_URL` as a private environment variable in the service settings, using the pooled value from Neon. Also set `SECRET_KEY`, `TEACHER_USERNAME`, and `TEACHER_PASSWORD` there. Do not commit `.env.local` or put database credentials in source control.

## Deploy to Heroku

Install the Heroku CLI, sign in with `heroku login`, then run these commands from this directory. Heroku detects `requirements.txt`, selects Python from `.python-version`, and launches Gunicorn using `Procfile`.

```sh
git init -b main
git add .
git commit -m "Initial CodeQuest deploy"
heroku create your-classroom-name
heroku addons:create heroku-postgresql:essential-0 -a your-classroom-name
heroku config:set SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')" -a your-classroom-name
heroku config:set TEACHER_USERNAME="teacher" TEACHER_PASSWORD="choose-a-private-password-of-at-least-12-characters" -a your-classroom-name
git push heroku main
heroku open -a your-classroom-name
```

The database add-on sets `DATABASE_URL` for the app. Choose a currently available Postgres plan in the dashboard if `essential-0` is unavailable.

## Security notes

- Each account has a unique username and a password hash. The server uses a signed, HTTP-only, same-site session cookie and takes the leaderboard username from the authenticated session, not from the browser’s score request.
- Choose distinct usernames and give account details to the intended students. Self-service registration does not verify a student’s real-world identity; teacher-created accounts or class invite codes can be added if required.
- The teacher panel at `/teacher` uses separate credentials from student accounts. Set `TEACHER_USERNAME` and a private `TEACHER_PASSWORD` (at least 12 characters) in the environment.
- The teacher panel shows only the most recent run per student and task. Grades are private to authenticated teacher sessions.
- The browser checks challenge answers, including additional input variants for algorithmic tasks, and submits points. This discourages hardcoded outputs such as `print(15)`, but a determined student can still modify browser code or requests. Do not treat the points as official grades or prizes without a trusted server-side evaluator.
- Set a stable, private `SECRET_KEY` in the deployment environment. The app refuses to start on Heroku without one.
- Python runs in the browser through Pyodide. When a signed-in student runs code, the latest code and its output are sent to the server so the teacher can review them. The student page does not expose teacher grades.
- SQLite is for local development. Use Postgres for a deployed app so leaderboard data persists across app restarts.
