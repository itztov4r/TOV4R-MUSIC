# TOV4R COMMUNITY

Bot Discord complet pour votre communauté, inspiré du fonctionnement Join to Create d’Ariyana. Code indépendant, identité TOV4R, messages Darija / français. Pas de module musique.

## Démarrage Windows — Mehdi

1. Installe **Python 3.12 ou plus récent** depuis https://www.python.org/downloads/ (inclure le lanceur `py`).
2. Dans https://discord.com/developers/applications, crée une **nouvelle application** nommée TOV4R COMMUNITY. La configuration du bot musique reste séparée.
3. Dans **Bot**, active **Server Members Intent** et **Message Content Intent**. Copie le token de ce nouveau bot. Ne le publie jamais dans un chat ou sur GitHub.
4. Copie **Application ID** depuis General Information. Active Developer Mode dans Discord > Advanced, puis clic droit sur ton serveur > Copy Server ID.
5. Décompresse le ZIP. Double-clique **START_WINDOWS.bat**. Le programme installe les dépendances et demande localement le token (saisie invisible), l’ID de l’application.
6. Ouvre le lien d’invitation affiché. Ajoute le bot à ton serveur. La permission Administrator n’est pas demandée.
7. Dans les rôles Discord, place **le rôle du bot au-dessus des rôles qu’il doit gérer ou modérer**. Il ne pourra pas modérer le propriétaire du serveur ou des membres placés plus haut.
8. Dans Discord, lance **`/setup`**. Cette commande crée les catégories Voice / Support, Join to Create, welcome, logs, les panels et le rôle TOV4R Support. Donne manuellement ce rôle aux membres de ton équipe. Aucun ancien salon n’est supprimé.
9. Ouvre **http://127.0.0.1:8080**. La clé de connexion est la valeur `DASHBOARD_KEY` du fichier `.env` créé sur ton PC. Le token Discord et cette clé sont deux secrets différents.

Si les commandes n’apparaissent pas : vérifier l’ID du serveur, l’invitation avec `applications.commands`, puis redémarrer Discord. Le bot synchronise toujours ses commandes globalement pour tous les serveurs. La propagation dépend de Discord. Si `DISCORD_GUILD_ID` provient de la version 1, les anciennes copies de commandes de ce serveur sont nettoyées automatiquement.

**Le bot reste connecté pendant que le processus tourne. Fermer la fenêtre, éteindre le PC ou le mettre en veille coupe le service. Le ZIP ne constitue pas une mise en ligne 24 h/24.**

## Tout ce qui est inclus

| Module | Fonctionnement |
| --- | --- |
| Temp Voice | Création automatique, déplacement du membre, suppression quand le salon est vide, nettoyage après redémarrage. Un salon par propriétaire. |
| Panel Voice | Lock / unlock, hide / show, rename, limite 0–99, autoriser un membre, kick + blocage, transfert du propriétaire, claim quand le propriétaire est parti. |
| Welcome / Goodbye | Carte PNG TOV4R générée à l’arrivée, message de bienvenue personnalisable, message de départ et Auto-role optionnel. |
| Tickets | Un ticket ouvert par membre, accès privé membre + support + admins, fermeture avec archivage et retrait d’accès du demandeur. Export texte par le support. |
| Modération | Warnings persistants, timeout / untimeout, kick, ban sans effacer l’historique, unban et purge. Vérifications des permissions et de la hiérarchie. |
| AutoMod | Anti-spam (6 messages en 7 secondes → suppression et tentative de timeout 1 minute), filtre d’invitations Discord optionnel, mots filtrés. Les modérateurs Manage Messages sont exemptés. |
| Logs | Arrivées, départs, édition / suppression de messages en cache, actions de modération du bot, tickets, création de salons, giveaways et changements du dashboard. |
| Levels | 20 XP par message éligible avec délai par membre / serveur, niveau = racine entière de XP / 100. Classement et carte texte de rang. |
| Giveaways | Durée, 1–20 gagnants, bouton rejoindre / quitter, tirage aléatoire de membres encore présents, fin automatique et reroll. Persistance après redémarrage. |
| Self Roles | Bouton d’ajout / retrait d’un rôle. Refus des rôles gérés, trop hauts ou possédant des permissions de modération / administration. |
| Annonces | Embeds personnalisés, sans notifications massives. |
| Dashboard privé | Vrais compteurs Discord / SQLite, paramètres, salons / rôles, activité et leaderboard. Aucune statistique fictive. |

Les membres explicitement autorisés peuvent entrer dans une room verrouillée. Les administrateurs Discord conservent leurs privilèges Discord. Le kick du panel bloque le retour de ce membre dans cette room jusqu’à ce qu’on utilise Permit. Le changement de nom reste soumis aux limites Discord.

## Les 22 commandes

| Commande | Accès |
| --- | --- |
| `/help`, `/invite`, `/rank`, `/leaderboard`, `/serverinfo` | Membres |
| `/setup`, `/configure`, `/panels` | Manage Server |
| `/rolepanel` | Manage Roles + rôle inférieur à celui de l’opérateur |
| `/announce`, `/purge` | Manage Messages |
| `/warn`, `/warnings`, `/timeout`, `/untimeout` | Moderate Members |
| `/kick` | Kick Members |
| `/ban`, `/unban` | Ban Members |
| `/giveaway`, `/giveaway_end`, `/giveaway_reroll` | Manage Server |
| `/transcript` | Rôle support ou Manage Server, à l’intérieur du ticket |

Utilise `/panels` pour renvoyer un panel supprimé. `/setup` réutilise les salons configurés et évite de republier les panels dans des salons déjà existants. Le dashboard permet de désactiver Auto-role, Welcome, Logs ou Join to Create en choisissant « Désactivé / Non configuré ».

Le message Welcome accepte `{mention}`, `{name}` et `{server}`. Les mentions s’affichent mais ne génèrent pas de notifications. Le filtre des mots est une recherche de sous-chaînes, insensible à la casse, pas un détecteur sémantique. Les salons fermés restent archivés : leur suppression est une action manuelle du staff.

## Dashboard et hébergement

Le dashboard est **un espace propriétaire avec clé**, pas un site public avec OAuth Discord. Sa clé donne accès à la configuration de tous les serveurs du bot. Elle reste uniquement en mémoire dans la page, sans localStorage. Il fonctionne dans le même processus que le bot, sans données de démonstration.

En local, il écoute seulement sur `127.0.0.1`. Ne publie pas directement le port en HTTP. Sur un VPS, le plus simple est de garder l’accès local et d’utiliser un tunnel SSH : `ssh -L 8080:127.0.0.1:8080 user@serveur`. Un accès web public nécessiterait un reverse proxy HTTPS correctement configuré.

### Linux

Avec Python 3.12+ et le paquet venv installés : `sh start.sh`. Le démarrage assisté crée `.env` si absent.

### Docker / VPS

Crée `.env` avec les mêmes variables que `.env.example`, puis :

```sh
docker compose up -d --build
docker compose logs -f
```

Le conteneur redémarre automatiquement (`unless-stopped`), tourne sans utilisateur root et conserve SQLite dans le volume `community-data`. Le port du dashboard reste lié au loopback de l’hôte. Ne lance pas deux instances avec le même token et la même base.

### Hébergeur de processus Python

Installation : `pip install -r requirements.txt`. Démarrage : `python bot.py`. Ajoute les variables dans l’interface privée de l’hébergeur ; configure un disque persistant et adapte `DATABASE_PATH`. GitHub Pages ne peut pas exécuter ce bot. Les offres gratuites pouvant dormir ou perdre les fichiers ne garantissent pas un fonctionnement continu.

## Données et sauvegardes

SQLite conserve configuration, propriétaires de salons, tickets, XP, warnings, giveaways, participations, panels de rôles et les 1000 derniers événements par serveur. Les logs peuvent contenir le texte de messages modifiés ou supprimés. Le salon des logs doit rester réservé au staff.

Les exports de tickets contiennent au maximum les **5000 derniers messages**, du plus récent au plus ancien, et les liens des pièces jointes (pas leurs fichiers). Ils sont envoyés en réponse privée au staff. Les logs d’édition / suppression ne couvrent que les messages disponibles dans le cache du bot ; ce n’est pas un archivage exhaustif de Discord.

Pour sauvegarder : arrêter le bot puis copier tout le dossier `data/` ou le volume Docker. Conserver `.env` séparément dans un espace privé. Ne pas utiliser `docker compose down -v` si tu veux conserver les données.

## Validation et limites de livraison

Les dépendances exactes sont verrouillées dans `requirements.txt`. Les tests locaux couvrent persistance, XP, permissions, contrôles de rôle, salon vide, tirages concurrents et reprise après panne, ainsi que l’authentification / routes du dashboard. Voir `TEST_REPORT.md`.

**Pas de connexion réelle à Discord effectuée pour cette livraison : aucun token de nouveau bot n’a été fourni.** Création réelle de salons, envoi des messages, modération, affichage des interactions et déploiement continu restent à valider avec le bot connecté au serveur. Le dashboard n’a pas fait l’objet d’un test visuel dans un navigateur.

Après connexion, vérifie dans un serveur de test : `/setup`, deux membres en voice, tous les boutons, ticket avec un membre normal et un support, message Welcome, XP, giveaway d’une minute, puis redémarrage du bot. Les suppressions de rooms vides et fins de giveaways sont récupérées environ toutes les 15 secondes après reconnexion.

## Développement

```sh
python -m unittest discover -s tests -v
```

`bot.py` : Discord / commandes / panels. `store.py` : données et validation. `dashboard.py` : API privée et serveur web. `web/` : interface responsive. `configure.py` : configuration locale sans affichage du token.

Références officielles consultées :
- https://discordpy.readthedocs.io/en/stable/interactions/api.html
- https://discordpy.readthedocs.io/en/stable/api.html


## Version multi-serveurs et préparation 24/7

`/invite` fournit le lien officiel pour ajouter le bot à un autre serveur. Active **Public Bot** dans Developer Portal > Bot pour permettre aux administrateurs d’autres serveurs de l’installer. Chaque serveur lance `/setup` et dispose de ses propres réglages, XP, tickets, rooms et giveaways. Aucun setup automatique ne modifie un serveur à l’arrivée du bot.

`railway.json` prépare une seule instance avec redémarrage automatique et mise en veille désactivée. Le lancement réel nécessite un token **du nouveau bot**, les intents activés, un service Railway et un volume persistant monté sur `/app/data`. Les coûts / limites du compte doivent être vérifiés avant activation ; ce fichier ne garantit pas un hébergement gratuit ou une disponibilité absolue.

Le service existant TOV4R-MUSIC a été identifié sur Railway. Il n’a pas été modifié. Aucun nouveau service n’a été lancé à ce stade : la connexion au Developer Portal est requise pour préparer le nouveau bot.
