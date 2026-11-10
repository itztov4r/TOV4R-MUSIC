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

L'image inclut `bot.py` **et** `server_setup.py`. Un contrôle d'import à la
construction évite de publier une image qui échoue au démarrage. Les commandes
vocales accusent réception avant la connexion ; arrêter ou quitter annule aussi
la préparation d'un morceau. La file reste limitée à 80 titres, y compris avec
plusieurs demandes simultanées.

Pour les accès DJ, `DJ_DB_PATH` peut viser un dossier de données (créé si
nécessaire). Sur un hébergement à disque éphémère, montez un stockage persistant
pour conserver ces accès lors d'un redéploiement. Aucun volume n'est créé
automatiquement par le bot.

## Vérification

```bash
python -m pip install -r requirements.txt pytest pytest-asyncio
python -m pytest -q
```

Les tests couvrent le démarrage depuis les fichiers Docker, l'enregistrement des
commandes sans réseau, les connexions concurrentes, l'annulation pendant la
préparation audio, les limites de file, les erreurs de lecture en boucle, SQLite,
la suppression des secrets dans les erreurs et un décodage PCM réel par FFmpeg.
Ils ne se connectent pas à un serveur Discord et n'utilisent aucun token.

Sur l'hébergement, vérifier ensuite la connexion Gateway et la synchronisation
des commandes dans les logs. Un test d'écoute nécessite un utilisateur dans un
salon vocal : `/testaudio`, `/testffmpeg`, puis `/sma3` avec un lien public autorisé.
Une source qui refuse les IP d'hébergement (HTTP 403) peut encore échouer ; le bot
signale cette erreur et passe au titre suivant au lieu de le boucler indéfiniment.

## Structure

- `bot.py` : code Discord et commandes.
- `requirements.txt` : dépendances Python.
- `Dockerfile` : conteneur de déploiement.
- `server_setup.py` : commandes de configuration facultatives, limitées au serveur autorisé.
- `index.html` : page de présentation responsive et sans dépendances.

## Sécurité

Ne publiez jamais de token Discord, de mot de passe, de clé privée ou de secret Spotify. N'utilisez pas d'informations réelles dans les captures ou les démonstrations publiques.

Projet de Mehdi Hmaiss / TOV4R.

## Recherche musicale et accès aux sources

`/sma3` avec un nom cherche d'abord sur SoundCloud, puis sur YouTube si le
premier moteur ne renvoie aucun résultat. Les résultats de recherche sont
vérifiés avec FFmpeg avant lecture ; un autre résultat est essayé si
l'extraction ou le décodage échoue. Le titre et le lien affichés correspondent
au résultat réellement sélectionné.

Un lien explicite conserve son enregistrement d'origine. Si YouTube refuse
l'accès depuis l'hébergement (« Sign in to confirm you're not a bot »), le bot
ne peut pas garantir sa lecture : utiliser une recherche par nom ou un lien
SoundCloud public / audio HTTPS accessible. Aucune session personnelle ni
cookie de navigateur n'est nécessaire pour la recherche publique SoundCloud.
