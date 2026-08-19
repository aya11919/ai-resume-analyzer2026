import sqlite3
import json
import os
from dotenv import load_dotenv

load_dotenv()
import database
import services

def reanalyze_all():
    conn = database.get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, filename, parsed_text FROM resumes")
    resumes = cursor.fetchall()
    print(f"Refetching {len(resumes)} resumes from active DB ({database.DB_PATH})...")

    for res in resumes:
        res_id = res['id']
        filename = res['filename']
        raw_text = res['parsed_text']
        
        # Clean text
        cleaned_text = services.clean_extracted_text(raw_text)
        
        print(f"\n--- Re-analyzing Resume #{res_id}: {filename} ---")
        try:
            ai_result = services.analyze_resume_with_ai(cleaned_text)
            print(f"Candidate: {ai_result.get('candidate_name')}")
            print(f"Score ATS: {ai_result.get('overall_score')}/100")
            print(f"Skills: {ai_result.get('skills')}")
            print(f"Projects count: {len(ai_result.get('projects', []))}")
            print(f"Experience count: {len(ai_result.get('experience', []))}")
            print(f"Education count: {len(ai_result.get('education', []))}")

            # Check if analysis record exists
            cursor.execute("SELECT id FROM analyses WHERE resume_id = ?", (res_id,))
            row = cursor.fetchone()

            if row:
                cursor.execute("""
                    UPDATE analyses
                    SET overall_score = ?,
                        summary = ?,
                        candidate_name = ?,
                        skills = ?,
                        experience = ?,
                        education = ?,
                        projects = ?,
                        certifications = ?,
                        languages = ?,
                        qualities = ?,
                        strengths = ?,
                        weaknesses = ?,
                        recommendations = ?,
                        status = 'completed'
                    WHERE id = ?
                """, (
                    ai_result.get("overall_score", 80),
                    ai_result.get("summary", ""),
                    ai_result.get("candidate_name", ""),
                    json.dumps(ai_result.get("skills", [])),
                    json.dumps(ai_result.get("experience", [])),
                    json.dumps(ai_result.get("education", [])),
                    json.dumps(ai_result.get("projects", [])),
                    json.dumps(ai_result.get("certifications", [])),
                    json.dumps(ai_result.get("languages", [])),
                    json.dumps(ai_result.get("qualities", [])),
                    json.dumps(ai_result.get("strengths", [])),
                    json.dumps(ai_result.get("weaknesses", [])),
                    json.dumps(ai_result.get("recommendations", [])),
                    row['id']
                ))
            else:
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
            
            # Also update clean parsed_text in resumes table
            cursor.execute("UPDATE resumes SET parsed_text = ? WHERE id = ?", (cleaned_text, res_id))
            conn.commit()
            print(f"SUCCESS: Analysis updated for '{filename}'!")

        except Exception as e:
            print(f"ERROR re-analyzing '{filename}': {e}")

    conn.close()
    print("\nAll database resumes successfully updated!")

if __name__ == "__main__":
    reanalyze_all()
