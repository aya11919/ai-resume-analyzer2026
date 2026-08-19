"""
Script de diagnostic — teste directement si services.py extrait bien
l'email et le téléphone, sans passer par le dashboard.

USAGE (depuis le dossier backend) :
    python test_email.py
"""
import services

# Texte de CV factice, avec un email et un téléphone bien visibles
texte_test = """
AYA IJENHA
Email : aya.test@example.com
Téléphone : 06 12 34 56 78
Casablanca, Maroc

PROFIL
Étudiante en 4ème année du cycle d'ingénieur en informatique.

COMPETENCES
Python, React, SQL
"""

print("=" * 60)
print("TEST 1 : analyse locale de secours (get_mock_analysis)")
print("=" * 60)
result_mock = services.get_mock_analysis(texte_test)
print("candidate_name:", repr(result_mock.get("candidate_name")))
print("email:", repr(result_mock.get("email")))
print("phone:", repr(result_mock.get("phone")))

print()
print("=" * 60)
print("TEST 2 : analyse complète (analyze_resume_with_ai, via Groq)")
print("=" * 60)
try:
    result_ai = services.analyze_resume_with_ai(texte_test)
    print("candidate_name:", repr(result_ai.get("candidate_name")))
    print("email:", repr(result_ai.get("email")))
    print("phone:", repr(result_ai.get("phone")))
except Exception as e:
    print("ERREUR:", e)