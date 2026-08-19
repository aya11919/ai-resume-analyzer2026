"""
Script de diagnostic — teste directement l'envoi d'email SMTP, sans
passer par le dashboard, pour isoler le problème.

USAGE (depuis le dossier backend) :
    python test_smtp.py ton_email@gmail.com
"""
import sys
import os
from dotenv import load_dotenv

load_dotenv()

if len(sys.argv) < 2:
    print("Usage : python test_smtp.py ton_email@gmail.com")
    sys.exit(1)

destinataire = sys.argv[1]

smtp_email = os.getenv("SMTP_EMAIL")
smtp_password = os.getenv("SMTP_APP_PASSWORD")

print("SMTP_EMAIL configuré :", repr(smtp_email))
print("SMTP_APP_PASSWORD configuré :", "OUI (masqué)" if smtp_password else "NON — MANQUANT")
print()

if not smtp_email or not smtp_password:
    print("ERREUR : SMTP_EMAIL ou SMTP_APP_PASSWORD manque dans le fichier .env")
    sys.exit(1)

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

msg = MIMEMultipart("alternative")
msg["Subject"] = "TEST — Diagnostic envoi email"
msg["From"] = smtp_email
msg["To"] = destinataire
msg.attach(MIMEText("Ceci est un email de test pour vérifier la configuration SMTP.", "plain"))

print(f"Tentative d'envoi vers {destinataire}...")
try:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(smtp_email, smtp_password)
        server.sendmail(smtp_email, destinataire, msg.as_string())
    print("SUCCES : email envoye sans erreur.")
except Exception as e:
    print("ECHEC : erreur lors de l'envoi :")
    print(repr(e))