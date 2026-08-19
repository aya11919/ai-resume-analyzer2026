"""
Script de diagnostic — vérifie ce que l'extracteur de texte PDF récupère
réellement d'un CV, sans envoyer le fichier à personne.

USAGE (depuis le dossier où se trouve services.py) :
    python check_extraction.py "chemin\\vers\\mon_cv.pdf"
"""
import sys

if len(sys.argv) < 2:
    print("Usage : python check_extraction.py chemin_vers_le_cv.pdf")
    sys.exit(1)

pdf_path = sys.argv[1]

try:
    import services
except ImportError:
    print("ERREUR : impossible d'importer services.py.")
    print("Lance ce script depuis le même dossier que services.py.")
    sys.exit(1)

with open(pdf_path, "rb") as f:
    file_bytes = f.read()

print(f"Fichier lu : {pdf_path} ({len(file_bytes)} octets)\n")

try:
    text = services.extract_text_from_pdf(file_bytes)
    print("=" * 60)
    print(f"TEXTE EXTRAIT ({len(text)} caractères) :")
    print("=" * 60)
    print(text[:2000])  # Affiche les 2000 premiers caractères
    if len(text) > 2000:
        print(f"\n... ({len(text) - 2000} caractères supplémentaires non affichés)")
except Exception as e:
    print(f"ÉCHEC DE L'EXTRACTION : {e}")