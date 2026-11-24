import os
import re
import secrets
import hmac
from datetime import timedelta

from flask import Flask, jsonify, request, send_from_directory, session
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect, text
from dotenv import load_dotenv
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLASS_NAMES = {"8В", "9А", "9Г"}
TEACHER_CLASS_NAMES = CLASS_NAMES | {"10Д"}
load_dotenv(os.path.join(BASE_DIR, ".env.local"))
IS_DEPLOYED = bool(os.environ.get("DYNO")) or os.environ.get("RENDER", "").lower() == "true"
app = Flask(__name__, static_folder=None)
secret_key = os.environ.get("SECRET_KEY")
if IS_DEPLOYED and not secret_key:
    raise RuntimeError("Set a stable SECRET_KEY in the deployment environment before starting the app.")
app.secret_key = secret_key or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "").lower() == "true" or IS_DEPLOYED,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)


@app.after_request
def set_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response

database_url = os.environ.get("DATABASE_URL")
if IS_DEPLOYED and not database_url:
    raise RuntimeError("Set DATABASE_URL to a persistent PostgreSQL database before starting the app.")
database_url = database_url or "sqlite:///classroom.db"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
if database_url.startswith("postgresql://"):
    database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
app.config.update(
    SQLALCHEMY_DATABASE_URI=database_url,
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True, "pool_recycle": 300},
)
db = SQLAlchemy(app)


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(24), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)


class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), unique=True, nullable=False)
    score = db.Column(db.Integer, nullable=False, default=0)
    class_name = db.Column(db.String(8), nullable=False, default="8В")


class Completion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    class_name = db.Column(db.String(8), nullable=False)
    challenge_id = db.Column(db.Integer, nullable=False)
    __table_args__ = (db.UniqueConstraint("account_id", "class_name", "challenge_id"),)


class ClassEnrollment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    class_name = db.Column(db.String(8), nullable=False)
    account = db.relationship(Account)
    __table_args__ = (db.UniqueConstraint("account_id", "class_name"),)


class SQLParticipant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, unique=True, index=True)
    account = db.relationship(Account)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=db.func.now())


class SQLCompletion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    challenge_id = db.Column(db.Integer, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=False, default=db.func.now())
    __table_args__ = (db.UniqueConstraint("account_id", "challenge_id"),)


class Submission(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    class_name = db.Column(db.String(8), nullable=False)
    challenge_id = db.Column(db.Integer, nullable=False)
    code = db.Column(db.Text, nullable=False)
    output = db.Column(db.Text, nullable=False, default="")
    is_correct = db.Column(db.Boolean, nullable=False, default=False)
    grade = db.Column(db.Integer, nullable=True)
    account = db.relationship(Account)
    updated_at = db.Column(db.DateTime, nullable=False, default=db.func.now(), onupdate=db.func.now())
    __table_args__ = (db.UniqueConstraint("account_id", "class_name", "challenge_id"),)


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/teacher")
def teacher_home():
    return send_from_directory(BASE_DIR, "teacher.html")


@app.get("/teacher/submissions")
def teacher_submissions_home():
    return send_from_directory(BASE_DIR, "teacher_submissions.html")


@app.get("/sql")
def sql_home():
    return send_from_directory(BASE_DIR, "sql.html")


def teacher_authenticated():
    return session.get("teacher_authenticated") is True


@app.post("/api/teacher/login")
def teacher_login():
    teacher_username = os.environ.get("TEACHER_USERNAME", "").strip()
    teacher_password = os.environ.get("TEACHER_PASSWORD", "")
    if not teacher_username or len(teacher_password) < 12:
        return jsonify(error="Вчительський вхід не налаштований. Задайте TEACHER_USERNAME і TEACHER_PASSWORD (від 12 символів)."), 503
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")
    if (not isinstance(username, str) or not isinstance(password, str) or
            not hmac.compare_digest(username.strip().casefold(), teacher_username.casefold()) or
            not hmac.compare_digest(password, teacher_password)):
        return jsonify(error="Неправильний логін або пароль."), 401
    session.clear()
    session.permanent = True
    session["teacher_authenticated"] = True
    return jsonify(ok=True)


@app.post("/api/teacher/logout")
def teacher_logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/teacher/submissions")
def teacher_submissions():
    if not teacher_authenticated():
        return jsonify(error="Потрібен вхід учителя."), 401
    class_name = request.args.get("class_name", "8В")
    if class_name not in CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400
    records = Submission.query.join(Account).filter(
        Submission.class_name == class_name
    ).order_by(Submission.updated_at.desc(), Account.username.asc()).all()
    return jsonify([{
        "id": row.id,
        "username": row.account.username,
        "class_name": row.class_name,
        "challenge_id": row.challenge_id,
        "code": row.code,
        "output": row.output,
        "is_correct": row.is_correct,
        "grade": row.grade,
        "updated_at": row.updated_at.isoformat(timespec="minutes"),
    } for row in records])


@app.get("/api/teacher/progress")
def teacher_progress():
    if not teacher_authenticated():
        return jsonify(error="Потрібен вхід учителя."), 401
    class_name = request.args.get("class_name", "8В")
    if class_name not in TEACHER_CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400

    if class_name == "10Д":
        participants = SQLParticipant.query.join(Account).order_by(Account.username.asc()).all()
        completed_by_account = {}
        for row in SQLCompletion.query.order_by(SQLCompletion.challenge_id.asc()):
            completed_by_account.setdefault(row.account_id, []).append(row.challenge_id)
        students = [{
            "username": participant.account.username,
            "completed_ids": completed_by_account.get(participant.account_id, []),
        } for participant in participants]
    else:
        account_ids = {row[0] for row in db.session.query(Completion.account_id).filter_by(class_name=class_name).distinct()}
        account_ids.update(row[0] for row in db.session.query(Submission.account_id).filter_by(class_name=class_name).distinct())
        account_ids.update(row[0] for row in db.session.query(ClassEnrollment.account_id).filter_by(class_name=class_name).distinct())
        accounts = Account.query.filter(Account.id.in_(account_ids)).order_by(Account.username.asc()).all() if account_ids else []
        completed_by_account = {}
        for row in Completion.query.filter_by(class_name=class_name).order_by(Completion.challenge_id.asc()):
            completed_by_account.setdefault(row.account_id, []).append(row.challenge_id)
        students = [{
            "username": account.username,
            "completed_ids": completed_by_account.get(account.id, []),
        } for account in accounts]

    score_by_name = {}
    for student in Student.query.filter_by(class_name=class_name).all():
        score_by_name[student.name.removeprefix(f"[{class_name}] ").casefold()] = student.score
    for student in students:
        student["completed_ids"] = sorted(set(student["completed_ids"]))
        student["completed_count"] = len(student["completed_ids"])
        student["score"] = len(student["completed_ids"]) * 50 if class_name == "10Д" else score_by_name.get(student["username"].casefold(), 0)
    students.sort(key=lambda student: (-student["completed_count"], -student["score"], student["username"].casefold()))
    return jsonify(class_name=class_name, total_tasks=16 if class_name == "9Г" else 10, students=students)


@app.post("/api/teacher/grade")
def teacher_grade():
    if not teacher_authenticated():
        return jsonify(error="Потрібен вхід учителя."), 401
    payload = request.get_json(silent=True) or {}
    submission_id = payload.get("submission_id")
    grade = payload.get("grade")
    if isinstance(submission_id, bool) or not isinstance(submission_id, int):
        return jsonify(error="Некоректне завдання."), 400
    if grade is not None and (isinstance(grade, bool) or not isinstance(grade, int) or not 1 <= grade <= 12):
        return jsonify(error="Оцінка має бути числом від 1 до 12 або порожньою."), 400
    record = db.session.get(Submission, submission_id)
    if record is None:
        return jsonify(error="Роботу не знайдено."), 404
    record.grade = grade
    db.session.commit()
    return jsonify(ok=True, grade=record.grade)


@app.post("/api/submissions")
def save_submission():
    account = current_student_account()
    if account is None:
        return jsonify(error="Увійдіть, щоб зберегти код для вчителя."), 401
    payload = request.get_json(silent=True) or {}
    class_name = payload.get("class_name")
    challenge_id = payload.get("challenge_id")
    code = payload.get("code")
    output = payload.get("output", "")
    is_correct = payload.get("is_correct", False)
    if class_name not in CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400
    if isinstance(challenge_id, bool) or not isinstance(challenge_id, int) or not 0 <= challenge_id < (16 if class_name == "9Г" else 10):
        return jsonify(error="Невідоме завдання."), 400
    if not isinstance(code, str) or len(code) > 50000:
        return jsonify(error="Код має бути текстом не довшим за 50 000 символів."), 400
    if not isinstance(output, str) or not isinstance(is_correct, bool):
        return jsonify(error="Некоректний результат запуску."), 400
    record = Submission.query.filter_by(
        account_id=account.id, class_name=class_name, challenge_id=challenge_id
    ).first()
    if record is None:
        record = Submission(
            account_id=account.id, class_name=class_name,
            challenge_id=challenge_id, code=code,
        )
        db.session.add(record)
    record.code = code
    record.output = output[:10000]
    record.is_correct = is_correct
    record.updated_at = db.func.now()
    db.session.commit()
    return jsonify(ok=True)


@app.get("/api/leaderboard")
def leaderboard():
    class_name = request.args.get("class_name", "8В")
    if class_name not in CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400
    students = Student.query.filter_by(class_name=class_name).order_by(
        Student.score.desc(), Student.name.asc()
    ).limit(50)
    return jsonify([
        {
            "name": student.name.removeprefix(f"[{class_name}] "),
            "score": student.score,
        }
        for student in students
    ])


def account_response(account):
    return jsonify(username=account.username)


@app.get("/api/me")
def current_account():
    account_id = session.get("account_id")
    if not account_id:
        return jsonify(error="Потрібен вхід."), 401
    account = db.session.get(Account, account_id)
    if account is None:
        session.clear()
        return jsonify(error="Профіль не знайдено."), 401
    return account_response(account)


@app.post("/api/register")
def register():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-zА-Яа-яІіЇїЄєҐґ0-9_.-]{3,24}", username):
        return jsonify(error="Логін має містити 3–24 літери, цифри або символи . _ - ."), 400
    username = username.casefold()
    if not isinstance(password, str) or len(password) < 10:
        return jsonify(error="Пароль має містити щонайменше 10 символів."), 400
    if Account.query.filter_by(username=username).first():
        return jsonify(error="Такий логін уже зайнятий. Увійдіть або оберіть інший."), 409
    # Reserve names that already appear in the legacy score table, so a new
    # account cannot take over an existing leaderboard identity.
    for student in Student.query.all():
        visible_name = student.name.removeprefix(f"[{student.class_name}] ")
        if visible_name.casefold() == username:
            return jsonify(error="Цей профіль уже є в таблиці лідерів. Зверніться до вчителя."), 409
    account = Account(username=username, password_hash=generate_password_hash(password))
    db.session.add(account)
    db.session.commit()
    session.clear()
    session.permanent = True
    session["account_id"] = account.id
    return account_response(account), 201


@app.post("/api/login")
def login():
    payload = request.get_json(silent=True) or {}
    username = payload.get("username")
    password = payload.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        return jsonify(error="Введіть логін і пароль."), 400
    account = Account.query.filter_by(username=username.strip().casefold()).first()
    if account is None or not check_password_hash(account.password_hash, password):
        return jsonify(error="Неправильний логін або пароль."), 401
    session.clear()
    session.permanent = True
    session["account_id"] = account.id
    return account_response(account)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


def current_student_account():
    account_id = session.get("account_id")
    return db.session.get(Account, account_id) if account_id else None


@app.get("/api/progress")
def get_progress():
    account = current_student_account()
    if account is None:
        return jsonify(error="Увійдіть, щоб зберігати прогрес."), 401
    class_name = request.args.get("class_name", "8В")
    if class_name not in CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400
    if ClassEnrollment.query.filter_by(account_id=account.id, class_name=class_name).first() is None:
        db.session.add(ClassEnrollment(account_id=account.id, class_name=class_name))
        db.session.commit()
    completed_ids = [row.challenge_id for row in Completion.query.filter_by(
        account_id=account.id, class_name=class_name
    ).order_by(Completion.challenge_id)]
    return jsonify(completed_ids=completed_ids)


@app.post("/api/progress")
def save_progress():
    account = current_student_account()
    if account is None:
        return jsonify(error="Увійдіть, щоб зберігати прогрес."), 401
    payload = request.get_json(silent=True) or {}
    class_name = payload.get("class_name", "8В")
    completed_ids = payload.get("completed_ids")
    if class_name not in CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400
    max_tasks = 16 if class_name == "9Г" else 10
    if (not isinstance(completed_ids, list) or len(completed_ids) > max_tasks or
            any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < max_tasks
                for value in completed_ids)):
        return jsonify(error="Некоректний список виконаних завдань."), 400
    if ClassEnrollment.query.filter_by(account_id=account.id, class_name=class_name).first() is None:
        db.session.add(ClassEnrollment(account_id=account.id, class_name=class_name))
    existing = {row.challenge_id for row in Completion.query.filter_by(
        account_id=account.id, class_name=class_name
    )}
    for challenge_id in set(completed_ids) - existing:
        db.session.add(Completion(
            account_id=account.id, class_name=class_name, challenge_id=challenge_id
        ))
    db.session.commit()
    return jsonify(completed_ids=sorted(existing | set(completed_ids)))


@app.get("/api/sql/progress")
def get_sql_progress():
    account = current_student_account()
    if account is None:
        return jsonify(error="Увійдіть, щоб зберігати прогрес 10Д."), 401
    participant = SQLParticipant.query.filter_by(account_id=account.id).first()
    if participant is None:
        db.session.add(SQLParticipant(account_id=account.id))
        db.session.commit()
    completed_ids = [row.challenge_id for row in SQLCompletion.query.filter_by(
        account_id=account.id
    ).order_by(SQLCompletion.challenge_id.asc())]
    return jsonify(completed_ids=completed_ids, score=len(completed_ids) * 50)


@app.post("/api/sql/progress")
def save_sql_progress():
    account = current_student_account()
    if account is None:
        return jsonify(error="Увійдіть, щоб зберігати прогрес 10Д."), 401
    payload = request.get_json(silent=True) or {}
    challenge_id = payload.get("challenge_id")
    if isinstance(challenge_id, bool) or not isinstance(challenge_id, int) or not 0 <= challenge_id < 10:
        return jsonify(error="Невідоме SQL-завдання."), 400
    if SQLParticipant.query.filter_by(account_id=account.id).first() is None:
        db.session.add(SQLParticipant(account_id=account.id))
    if SQLCompletion.query.filter_by(account_id=account.id, challenge_id=challenge_id).first() is None:
        db.session.add(SQLCompletion(account_id=account.id, challenge_id=challenge_id))
    db.session.commit()
    completed_ids = [row.challenge_id for row in SQLCompletion.query.filter_by(
        account_id=account.id
    ).order_by(SQLCompletion.challenge_id.asc())]
    return jsonify(completed_ids=completed_ids, score=len(completed_ids) * 50)


@app.get("/api/sql/leaderboard")
def sql_leaderboard():
    participants = SQLParticipant.query.join(Account).order_by(Account.username.asc()).all()
    counts = dict(db.session.query(SQLCompletion.account_id, db.func.count(SQLCompletion.id)).group_by(
        SQLCompletion.account_id
    ).all())
    entries = [{"name": participant.account.username, "score": counts.get(participant.account_id, 0) * 50}
               for participant in participants]
    entries.sort(key=lambda entry: (-entry["score"], entry["name"].casefold()))
    return jsonify(entries[:50])


@app.post("/api/score")
def update_score():
    account_id = session.get("account_id")
    account = db.session.get(Account, account_id) if account_id else None
    if account is None:
        return jsonify(error="Увійдіть, щоб зберігати бали."), 401
    payload = request.get_json(silent=True) or {}
    score = payload.get("score")
    class_name = payload.get("class_name", "8В")
    if isinstance(score, bool) or not isinstance(score, int) or score < 0 or score > 5000:
        return jsonify(error="Некоректна кількість балів."), 400
    if class_name not in CLASS_NAMES:
        return jsonify(error="Невідомий клас."), 400

    name = account.username
    stored_name = f"[{class_name}] {name}"
    student = Student.query.filter_by(name=stored_name, class_name=class_name).first()
    if student is None:
        # Upgrade a display-name record created by the earlier single-class MVP.
        student = Student.query.filter_by(name=name, class_name=class_name).first()
        if student is not None:
            student.name = stored_name
    if student is None:
        student = Student(name=stored_name, score=score, class_name=class_name)
        db.session.add(student)
    else:
        student.score = max(student.score, score)
    db.session.commit()
    return jsonify(name=name, score=student.score)


with app.app_context():
    db.create_all()
    columns = {column["name"] for column in inspect(db.engine).get_columns("student")}
    if "class_name" not in columns:
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE student ADD COLUMN class_name VARCHAR(8) NOT NULL DEFAULT '8В'"))
    if db.engine.dialect.name == "postgresql":
        with db.engine.begin() as connection:
            connection.execute(text("ALTER TABLE student ALTER COLUMN name TYPE VARCHAR(40)"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
