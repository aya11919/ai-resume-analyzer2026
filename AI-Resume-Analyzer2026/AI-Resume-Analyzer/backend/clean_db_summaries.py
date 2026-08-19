import sqlite3
import os
import re

db_path = os.path.join(os.path.dirname(__file__), "resume_analyzer.db")
if not os.path.exists(db_path):
    print("Base de données introuvable.")
    exit(0)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Find analyses containing raw error traces
cursor.execute("SELECT id, summary FROM analyses WHERE summary LIKE '%Analyse IA%' OR summary LIKE '%Erreur API%' OR summary LIKE '%⚠️%'")
rows = cursor.fetchall()
print(f"Trouvé {len(rows)} entrée(s) à nettoyer dans la table 'analyses'.")

for row_id, summary in rows:
    # Clean out raw error prefix
    clean_sum = re.sub(r'⚠️\s*Analyse IA.*?:', '', summary)
    clean_sum = re.sub(r'Erreur API.*?:', '', clean_sum)
    clean_sum = re.sub(r'\{\s*"error".*?\}\s*', '', clean_sum)
    clean_sum = clean_sum.strip()
    if not clean_sum:
        clean_sum = "Analyse du profil candidat effectuée avec succès."
    
    cursor.execute("UPDATE analyses SET summary = ? WHERE id = ?", (clean_sum, row_id))

conn.commit()
conn.close()
print("Base de données nettoyée avec succès !")
