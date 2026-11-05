# TOV4R MUSIC — Discord Music Bot

Projet Python / Discord pour la lecture musicale et l'administration d'une file d'attente dans les salons vocaux.

**Liens**

- [Présentation mobile / desktop de ce dépôt](https://itztov4r.github.io/TOV4R-MUSIC/)
- [Site complet TOV4R MUSIC, FAQ et lecteur de démonstration](https://itztov4r.github.io/Tov4r-bot-/)
- [Inviter le bot Discord](https://discord.com/oauth2/authorize?client_id=1567328279265017968)

La page `index.html` est **statique** et s'adapte aux téléphones, tablettes et ordinateurs. Elle n'exécute **pas** le bot. La disponibilité du bot dépend de son processus et de son hébergement.

## Fonctions visibles dans le code

Commandes de base : `/ping`, `/sma3`, `/skot`, `/kml`, `/next`, `/saf`, `/list`, `/chno`, `/dour`, `/mix`, `/volume`, `/khrej`.

Outils de diagnostic : `/status`, `/testaudio`, `/testffmpeg`, `/testlink`, `/spotifyinfo`. Certaines commandes / fonctions dépendent de sources ou services externes ; leur succès n'est pas garanti sur tous les liens.

Mode DJ : `/dj`, ainsi que les commandes de gestion d'accès réservées au propriétaire. Ce dépôt ne traite pas directement les paiements.

## Installation de développement

Prérequis : Python 3.13, FFmpeg, Node.js 22+ et dépendances de `requirements.txt`.

```bash
python -m pip install -r requirements.txt
python bot.py
```

Configurez les variables d'environnement nécessaires à votre propre hébergement, notamment `DISCORD_TOKEN`. Gardez les secrets dans les variables protégées de votre hébergeur ou dans un fichier local non suivi par Git.

Le `Dockerfile` installe Python, Node.js et FFmpeg, mais ne fournit pas d'hébergement gratuit 24/7 à lui seul.

## Structure

- `bot.py` : code Discord et commandes.
- `requirements.txt` : dépendances Python.
- `Dockerfile` : conteneur de déploiement.
- `index.html` : page de présentation responsive et sans dépendances.

## Sécurité

Ne publiez jamais de token Discord, de mot de passe, de clé privée ou de secret Spotify. N'utilisez pas d'informations réelles dans les captures ou les démonstrations publiques.

Projet de Mehdi Hmaiss / TOV4R.