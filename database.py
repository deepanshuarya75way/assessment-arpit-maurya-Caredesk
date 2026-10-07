import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config

# db file sits next to this script, so it doesn't matter where uvicorn is started from
DB_PATH = Path(__file__).resolve().parent / "healthcare.db"


@contextmanager
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_tables():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                age INTEGER,
                created at TEXT DEFAULT CUREENT_TIMESTAMP, FOREIGN KEY (user_id)REFERENCES users(id)REFERENCE user(id)
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                role text NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT DEAFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (patient_id) REFERENCES patients (id)
            )
        """)

        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
            salt TEXT NOT NULL, pw_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        # older databases: add created_at, and remove the old plain-text password column
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
        if "created_at" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
        if "password_plain" in cols:
            try:
                conn.execute("ALTER TABLE users DROP COLUMN password_plain")
            except sqlite3.OperationalError:
                conn.execute("UPDATE users SET password_plain = NULL")
        conn.execute("""CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS doctors (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, department TEXT NOT NULL)""")

        # DB-level guard: two people can never hold the same booked slot
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_booked_slot
            ON appointments (department, appointment_date, appointment_time)
            WHERE status = 'Booked'
        """)

        # simple log so the dashboard can show real numbers
        conn.execute("""
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)


# ---------- patients ----------

def create_conversation(user_id):
    with get_connection() as conn:
        return conn.execute(
            "SELECT*FROM conversation WHERE id =?",(connection_id,)
        ).fetchone
        return dic(row) if row else None
        def add_message(conversation_id, role,content):
            with get_connection()as conn:
                conn.execute(
                    "INSERT INTO chat_message(converation_id, role, content) VALUE(?,?,?)",
                    (conversation_id, role, content),
                )
                 def get_messages(conversation_id, limit=20):        
            with get_connection()as conn:
                rows= conn.execute()as conn:
                rows= conn.excute(
                    """SELECT role, content FROM chats_message WHERE conversation_id=?
                    ORDER BY ID DESC LIMIT?""
                (convseration_id ,limit),
                ). fetchall()
                return[dict(r)for r in rows][::-1]


def get_patients():
    with get_connection() as conn:
        rows = conn.execute("SELECT * FROM patients ORDER BY id DESC").fetchall()
    return [dict(row) for row in rows]


def get_patient(patient_id):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM patients WHERE id = ?", (patient_id,)
        ).fetchone()
    return dict(row) if row else None


# ---------- appointments ----------

def slot_taken(department, appointment_date, appointment_time):
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT id FROM appointments
            WHERE department = ? AND appointment_date = ? AND appointment_time = ?
              AND status = 'Booked'
            """,
            (department, appointment_date, appointment_time),
        ).fetchone()
    return row is not None


def add_appointment(patient, department, doctor, appointment_date, appointment_time):
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO appointments
            (patient_id, patient_name, email, department, doctor,
             appointment_date, appointment_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'Booked')
            """,
            (
                patient["id"],
                patient["name"],
                patient["email"],
                department,
                doctor,
                appointment_date,
                appointment_time,
            ),
        )
        return cursor.lastrowid


def get_appointments():
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, patient_id, patient_name, email, department, doctor,
                   appointment_date, appointment_time, status
            FROM appointments
            ORDER BY appointment_date, appointment_time
            """
        ).fetchall()
    return [dict(row) for row in rows]


def cancel_appointment(appointment_id):
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE appointments SET status = 'Cancelled' WHERE id = ? AND status = 'Booked'",
            (appointment_id,),
        )
        return cur.rowcount > 0


# ---------- activity / stats ----------

def log_activity(kind):
    with get_connection() as conn:
        conn.execute("INSERT INTO activity (kind) VALUES (?)", (kind,))


def get_stats():
    with get_connection() as conn:
        patients = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        appointments = conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0]
        reports = conn.execute(
            "SELECT COUNT(*) FROM activity WHERE kind = 'report'"
        ).fetchone()[0]
        ai_queries = conn.execute(
            "SELECT COUNT(*) FROM activity WHERE kind = 'ai_query'"
        ).fetchone()[0]

    return {
        "patients": patients,
        "appointments": appointments,
        "reports_processed": reports,
        "ai_queries": ai_queries,
    }


# ---------- auth ----------

def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 120000).hex()


def create_user(username, password):
    """Saves only a salted hash of the password. Raises sqlite3.IntegrityError if the name exists."""
    salt = secrets.token_hex(16)
    with get_connection() as conn:
        return conn.execute(
            "INSERT INTO users (username, salt, pw_hash) VALUES (?, ?, ?)",
            (username, salt, _hash(password, salt))).lastrowid


def get_users():
    """For the admin page: id, username and signup time (no passwords)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT id, username, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def login(username, password):
    with get_connection() as conn:
        u = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not u or not secrets.compare_digest(_hash(password, u["salt"]), u["pw_hash"]):
            return None
        conn.execute("DELETE FROM sessions WHERE created_at < datetime('now', '-7 days')")  # clean old sessions
        token = secrets.token_urlsafe(32)
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, u["id"]))
        return token


def user_from_token(token):
    if not token:
        return None
    with get_connection() as conn:
        row = conn.execute(
            """SELECT u.id, u.username FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token = ? AND s.created_at > datetime('now', '-7 days')""", (token,)
        ).fetchone()
    return dict(row) if row else None


def logout(token):
    with get_connection() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def change_password(user_id, old, new):
    with get_connection() as conn:
        u = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not u or not secrets.compare_digest(_hash(old, u["salt"]), u["pw_hash"]):
            return False
        salt = secrets.token_hex(16)
        conn.execute("UPDATE users SET salt = ?, pw_hash = ? WHERE id = ?", (salt, _hash(new, salt), user_id))
        return True


def _force_set_password(user_id, new_password):
    """Sets a user's password without needing the old one. Used only to keep the
    admin account in sync with the ADMIN_PASSWORD in .env on every startup."""
    salt = secrets.token_hex(16)
    with get_connection() as conn:
        conn.execute("UPDATE users SET salt = ?, pw_hash = ? WHERE id = ?",
                     (salt, _hash(new_password, salt), user_id))


def seed_defaults():
    """Runs every time the app starts.
    - Makes sure the admin account exists, and always matches the password in .env
      (so the admin login never gets "stuck" on an old password from a previous run).
    - Adds one doctor per department, the first time only.
    """
    with get_connection() as conn:
        admin_row = conn.execute(
            "SELECT id FROM users WHERE username = ?", (config.ADMIN_USERNAME,)).fetchone()
        has_doc = conn.execute("SELECT COUNT(*) FROM doctors").fetchone()[0]

    password = config.ADMIN_PASSWORD
    if not password:
        password = secrets.token_urlsafe(9)
        print(f"\n[CareDesk] ADMIN_PASSWORD is not set in .env. Generated admin login -> "
              f"username: {config.ADMIN_USERNAME}  password: {password}\n")

    if not admin_row:
        create_user(config.ADMIN_USERNAME, password)
    elif config.ADMIN_PASSWORD:
        # keep the admin password in sync with .env every time the server starts
        _force_set_password(admin_row["id"], password)

    if not has_doc:
        for n, dep in [("Dr. Sharma", "General Medicine"), ("Dr. Mehta", "Cardiology"),
                       ("Dr. Kapoor", "Dermatology"), ("Dr. Rao", "Neurology"), ("Dr. Singh", "Orthopedics")]:
            add_doctor(n, dep)


# ---------- doctors ----------

def add_doctor(name, department):
    with get_connection() as conn:
        return conn.execute("INSERT INTO doctors (name, department) VALUES (?, ?)", (name, department)).lastrowid


def get_doctors():
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM doctors ORDER BY department, name")]


def delete_doctor(doctor_id):
    with get_connection() as conn:
        return conn.execute("DELETE FROM doctors WHERE id = ?", (doctor_id,)).rowcount > 0


# ---------- patient edit / delete ----------

def update_patient(pid, name, age, gender, email, phone, department, symptoms):
    with get_connection() as conn:
        cur = conn.execute(
            """UPDATE patients SET name=?, age=?, gender=?, email=?, phone=?, department=?, symptoms=?
               WHERE id=?""", (name, age, gender, email, phone, department, symptoms, pid))
        if cur.rowcount:  # keep appointment copies in sync
            conn.execute("UPDATE appointments SET patient_name=?, email=? WHERE patient_id=?", (name, email, pid))
        return cur.rowcount > 0


def delete_patient(pid):
    with get_connection() as conn:
        conn.execute("DELETE FROM appointments WHERE patient_id = ?", (pid,))
        return conn.execute("DELETE FROM patients WHERE id = ?", (pid,)).rowcount > 0
