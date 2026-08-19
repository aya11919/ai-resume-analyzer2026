import sqlite3

conn = sqlite3.connect('resume_analyzer.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""
    SELECT a.id, r.filename, a.overall_score, a.created_at
    FROM analyses a
    JOIN resumes r ON a.resume_id = r.id
    ORDER BY a.created_at DESC
""")
rows = cur.fetchall()
if not rows:
    print("Aucune analyse trouvée dans la base de données.")
for r in rows:
    print(f"ID:{r['id']} | Fichier:{r['filename']} | Score:{r['overall_score']} | Date:{r['created_at']}")
conn.close()
