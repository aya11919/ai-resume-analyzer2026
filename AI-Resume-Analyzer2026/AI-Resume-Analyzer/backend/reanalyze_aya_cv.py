import sqlite3
import json
import os
import services

db_path = os.path.join(os.path.dirname(__file__), "resume_analyzer.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("SELECT r.id, r.filename, r.parsed_text, a.id FROM resumes r JOIN analyses a ON r.id = a.resume_id WHERE r.filename LIKE '%AYA%' ORDER BY r.id DESC LIMIT 1")
row = cursor.fetchone()

if row:
    resume_id, filename, parsed_text, analysis_id = row
    print(f"Re-analyzing '{filename}' (Resume ID: {resume_id}, Analysis ID: {analysis_id})...")
    
    # Run upgraded AI analysis
    ai_result = services.analyze_resume_with_ai(parsed_text)
    print("New AI overall score:", ai_result.get("overall_score"))
    print("Extracted skills:", ai_result.get("skills"))
    print("Extracted projects:", len(ai_result.get("projects", [])))
    print("Extracted languages:", ai_result.get("languages"))
    print("Extracted certifications:", len(ai_result.get("certifications", [])))

    # Update database record
    cursor.execute("""
        UPDATE analyses
        SET overall_score = ?,
            summary = ?,
            skills = ?,
            experience = ?,
            education = ?,
            strengths = ?,
            weaknesses = ?,
            recommendations = ?
        WHERE id = ?
    """, (
        ai_result.get("overall_score", 80),
        ai_result.get("summary", ""),
        json.dumps(ai_result.get("skills", [])),
        json.dumps(ai_result.get("experience", [])),
        json.dumps(ai_result.get("education", [])),
        json.dumps(ai_result.get("strengths", [])),
        json.dumps(ai_result.get("weaknesses", [])),
        json.dumps(ai_result.get("recommendations", [])),
        analysis_id
    ))
    conn.commit()
    print("Successfully updated database record for Aya Ijenha's CV!")

conn.close()
