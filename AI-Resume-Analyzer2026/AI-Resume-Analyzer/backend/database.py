import sqlite3
import os

_DATA_DIR = os.path.join(os.path.expanduser("~"), "AIResumeAnalyzerData")
os.makedirs(_DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(_DATA_DIR, "resume_analyzer.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS resumes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT NOT NULL,
        parsed_text TEXT NOT NULL,
        uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        resume_id INTEGER NOT NULL,
        overall_score INTEGER DEFAULT 0,
        summary TEXT,
        skills TEXT,
        experience TEXT,
        education TEXT,
        strengths TEXT,
        weaknesses TEXT,
        recommendations TEXT,
        job_description_match TEXT,
        chat_history TEXT DEFAULT '[]',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (resume_id) REFERENCES resumes (id) ON DELETE CASCADE
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        password_salt TEXT NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0,
        verification_code TEXT,
        code_expiry TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS interviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        analysis_id INTEGER NOT NULL,
        interview_date TEXT NOT NULL,
        interview_time TEXT NOT NULL,
        format TEXT NOT NULL,
        location_link TEXT,
        notes TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (analysis_id) REFERENCES analyses (id) ON DELETE CASCADE
    )
    """)

    conn.commit()

    cursor.execute("PRAGMA table_info(analyses)")
    existing_columns = {row[1] for row in cursor.fetchall()}

    migrations = [
        ("candidate_name", "ALTER TABLE analyses ADD COLUMN candidate_name TEXT"),
        ("projects",       "ALTER TABLE analyses ADD COLUMN projects TEXT"),
        ("certifications", "ALTER TABLE analyses ADD COLUMN certifications TEXT"),
        ("languages",      "ALTER TABLE analyses ADD COLUMN languages TEXT"),
        ("qualities",      "ALTER TABLE analyses ADD COLUMN qualities TEXT"),
        ("status",         "ALTER TABLE analyses ADD COLUMN status TEXT DEFAULT 'completed'"),
        ("match_score",    "ALTER TABLE analyses ADD COLUMN match_score INTEGER"),
        ("job_title",      "ALTER TABLE analyses ADD COLUMN job_title TEXT"),
        ("review_status",  "ALTER TABLE analyses ADD COLUMN review_status TEXT DEFAULT 'a_etudier'"),
        ("email",          "ALTER TABLE analyses ADD COLUMN email TEXT"),
        ("phone",          "ALTER TABLE analyses ADD COLUMN phone TEXT"),
        ("city",           "ALTER TABLE analyses ADD COLUMN city TEXT"),
        ("job_reference",    "ALTER TABLE analyses ADD COLUMN job_reference TEXT"),
        ("education_level",  "ALTER TABLE analyses ADD COLUMN education_level TEXT"),
    ]
    for col_name, sql in migrations:
        if col_name not in existing_columns:
            cursor.execute(sql)
    conn.commit()

    # Migrations pour la table interviews
    cursor.execute("PRAGMA table_info(interviews)")
    interview_columns = {row[1] for row in cursor.fetchall()}
    interview_migrations = [
        ("job_title", "ALTER TABLE interviews ADD COLUMN job_title TEXT"),
        ("candidate_name", "ALTER TABLE interviews ADD COLUMN candidate_name TEXT"),
    ]
    for col_name, sql in interview_migrations:
        if col_name not in interview_columns:
            cursor.execute(sql)
    conn.commit()

    # Backfill missing job_title from job_description_match if available
    try:
        import json
        cursor.execute("SELECT id, job_description_match, job_title FROM analyses WHERE job_title IS NULL OR job_title = ''")
        rows = cursor.fetchall()
        for r in rows:
            match_str = r['job_description_match']
            if match_str:
                try:
                    data = json.loads(match_str)
                    roles = data.get('fitting_roles', [])
                    if roles and len(roles) > 0 and roles[0]:
                        cursor.execute("UPDATE analyses SET job_title = ? WHERE id = ?", (roles[0], r['id']))
                except Exception:
                    pass
        conn.commit()
    except Exception as e:
        pass

    conn.close()

if __name__ == "__main__":
    init_db()
    print("Database initialized successfully.")