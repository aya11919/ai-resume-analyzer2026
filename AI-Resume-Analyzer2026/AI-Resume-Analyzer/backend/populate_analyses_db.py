import sqlite3
import json
import os
from dotenv import load_dotenv

load_dotenv()
import services

db_path = os.path.join(os.path.dirname(__file__), "resume_analyzer.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT id, filename, parsed_text FROM resumes")
resumes = cursor.fetchall()
print(f"Trouve {len(resumes)} CV dans la table 'resumes'.")

for res_id, filename, parsed_text in resumes:
    print(f"Analyse en cours pour '{filename}' (ID: {res_id})...")
    try:
        ai_result = services.analyze_resume_with_ai(parsed_text)
        cursor.execute("""
            INSERT INTO analyses (
                resume_id, overall_score, summary, skills, experience,
                education, strengths, weaknesses, recommendations, job_description_match
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            res_id,
            ai_result.get("overall_score", 80),
            ai_result.get("summary", ""),
            json.dumps(ai_result.get("skills", [])),
            json.dumps(ai_result.get("experience", [])),
            json.dumps(ai_result.get("education", [])),
            json.dumps(ai_result.get("strengths", [])),
            json.dumps(ai_result.get("weaknesses", [])),
            json.dumps(ai_result.get("recommendations", [])),
            json.dumps(ai_result.get("job_description_match"))
        ))
        print(f"OK: Analyse creee pour '{filename}' avec score ATS: {ai_result.get('overall_score')}/100.")
    except Exception as e:
        print(f"ERROR: Echec pour '{filename}': {e}")

conn.commit()
conn.close()
print("Populating DB analyses termine avec succes !")
