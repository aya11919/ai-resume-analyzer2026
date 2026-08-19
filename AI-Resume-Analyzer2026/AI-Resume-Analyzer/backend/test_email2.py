"""
Script de diagnostic avancé — extrait le texte du VRAI CV, puis teste
l'extraction d'email/téléphone dessus (au lieu d'un texte fictif).

USAGE (depuis le dossier backend) :
    python test_email2.py "chemin\\vers\\le_cv.pdf"
"""
import sys
import re
import services

if len(sys.argv) < 2:
    print("Usage : python test_email2.py chemin_vers_le_cv.pdf")
    sys.exit(1)

pdf_path = sys.argv[1]

with open(pdf_path, "rb") as f:
    file_bytes = f.read()

print("Extraction du texte en cours...")
resume_text = services.extract_text(file_bytes, pdf_path)
print(f"Texte extrait : {len(resume_text)} caractères\n")

# Affiche les lignes contenant potentiellement un email ou un numéro,
# pour voir EXACTEMENT comment c'est écrit dans le vrai texte extrait.
print("=" * 60)
print("Lignes du texte contenant '@' ou des chiffres groupés :")
print("=" * 60)
for line in resume_text.split('\n'):
    if '@' in line or re.search(r'\d{2}[\s.-]?\d{2}[\s.-]?\d{2}', line):
        print(repr(line))

print()
print("=" * 60)
print("TEST : get_mock_analysis sur le VRAI texte")
print("=" * 60)
result = services.get_mock_analysis(resume_text)
print("candidate_name:", repr(result.get("candidate_name")))
print("email:", repr(result.get("email")))
print("phone:", repr(result.get("phone")))

# Test direct des regex utilisées, pour voir si elles matchent
print()
print("=" * 60)
print("TEST DIRECT DES REGEX (indépendant de get_mock_analysis)")
print("=" * 60)
email_match = re.search(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', resume_text)
print("Regex email trouve :", repr(email_match.group(0)) if email_match else "AUCUN MATCH")

phone_match = re.search(r'(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{2,4}[\s.-]?\d{2,4}[\s.-]?\d{0,4}', resume_text)
print("Regex telephone trouve :", repr(phone_match.group(0)) if phone_match else "AUCUN MATCH")