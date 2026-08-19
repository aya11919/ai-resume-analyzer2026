# 📄 AI Resume Analyzer 2026

Une plateforme intelligente de recrutement conçue pour automatiser l'analyse de CV, le tri des candidatures et le suivi des entretiens grâce à l'Intelligence Artificielle (Groq / Llama 3.3).

<!-- Ajoutez ici une capture d'écran à jour une fois le site déployé, par exemple :
![Aperçu de l'application](apercu.png) -->

## 🌟 Fonctionnalités Principales

- **Analyse Automatique par IA** : Téléchargez un CV (PDF ou DOCX), l'IA extrait automatiquement les compétences, l'expérience, la formation, les projets, les certifications et les langues, puis calcule un score ATS sur 100.
- **Matching avec une offre d'emploi** : Collez ou importez une fiche de poste pour obtenir un score de correspondance, les mots-clés manquants et les rôles suggérés.
- **Analyse par lot** : Analysez jusqu'à 20 CV en une seule fois pour un même poste.
- **Assistant RH interactif** : Discutez directement avec un CV analysé pour poser des questions ciblées ("Quelle est son expérience en Python ?", "Quels sont ses points faibles ?").
- **Planification d'entretiens** : Interface calendrier pour programmer un entretien (visio, présentiel, téléphone), avec envoi automatique de l'email de convocation au candidat.
- **Suivi du pipeline de recrutement** : Statuts "à étudier", "en entretien", "retenu", "refusé" — avec email de refus généré et envoyé automatiquement.
- **Tableau de bord** : KPIs en temps réel (CV analysés, score ATS moyen, score de matching moyen).
- **Générateur d'affiche de poste** : Génère une image partageable (avec aperçu Open Graph pour LinkedIn) à partir d'une offre.
- **Comptes utilisateurs sécurisés** : Inscription avec vérification par code envoyé par email, connexion protégée par session — les données des candidats ne sont accessibles qu'aux utilisateurs connectés.

## 🛠️ Stack technique

**Frontend**
- HTML / CSS / JavaScript natifs (aucun framework, aucune étape de build)
- [Chart.js](https://www.chartjs.org/) pour les graphiques du tableau de bord
- [html2canvas](https://html2canvas.hertzen.com/) pour l'export d'affiches en image

**Backend**
- Python 3 — serveur HTTP écrit avec la bibliothèque standard (`http.server`), sans framework web externe (pas de FastAPI/Flask)
- Base de données : SQLite, via `sqlite3` (accès direct, sans ORM)
- IA : [Groq API](https://console.groq.com/) (modèles Llama 3.3 / Qwen)
- Extraction de texte : [PyMuPDF](https://pymupdf.readthedocs.io/) et [pypdf](https://pypdf.readthedocs.io/) pour les PDF avec texte natif, avec repli automatique sur l'OCR ([Tesseract](https://tesseract-ocr.github.io/) via `pytesseract`) pour les CV scannés ou sans texte extractible
- Emails : envoi via SMTP (Gmail) pour les codes de vérification et les convocations d'entretien

## 🚀 Installation et lancement en local

### 1. Prérequis

- [Python](https://www.python.org/) 3.9 ou supérieur
- Une clé API Groq valide ([console.groq.com/keys](https://console.groq.com/keys))
- Un compte Gmail avec un [mot de passe d'application](https://myaccount.google.com/apppasswords) (pour l'envoi des emails)

### 2. Configuration de l'environnement

Dans `AI-Resume-Analyzer/backend`, copiez `.env.template` vers `.env` et complétez-le :

```
GROQ_API_KEY=votre_cle_api_groq
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_VISION_MODEL=llama-3.3-70b-versatile
SMTP_EMAIL=votre_email@gmail.com
SMTP_APP_PASSWORD=votre_mot_de_passe_application
DATABASE_URL=sqlite:///./resume_analyzer.db
```

⚠️ Ce fichier `.env` contient des secrets : il ne doit jamais être poussé sur GitHub (il est déjà exclu via `.gitignore`).

### 3. Lancer le backend

```bash
cd AI-Resume-Analyzer/backend
pip install -r requirements.txt
python main.py
```

Le backend est accessible sur `http://127.0.0.1:8000`.

### 4. Lancer le frontend

Aucune installation n'est nécessaire : ouvrez simplement `AI-Resume-Analyzer/frontend/index.html` dans votre navigateur (ou utilisez l'extension "Live Server" de VS Code, déjà configurée dans `.vscode/settings.json`).

## ☁️ Déploiement

Le backend et le frontend sont conçus pour être déployés séparément (par exemple backend sur [Render](https://render.com), frontend sur Render ou GitHub Pages). Avant de déployer, pensez à mettre à jour `window.BACKEND_URL` en haut de `frontend/index.html` avec l'adresse de votre backend en ligne.

⚠️ L'OCR (repli pour les CV scannés) nécessite le programme `tesseract` installé au niveau du système, en plus du paquet Python `pytesseract`. Sur l'offre Python standard de Render, ce binaire n'est pas présent — l'OCR ne fonctionnera donc que si vous déployez via un `Dockerfile` qui l'installe. L'extraction pour les PDF avec texte natif (la grande majorité des CV) fonctionne normalement sans cette contrainte.

## 📝 Licence

Projet développé dans le cadre d'un projet de fin d'année (PFA) 2026. Toute reproduction sans autorisation est interdite.
