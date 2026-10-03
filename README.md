# 🦃 DindonBot

> **Un compagnon IA personnel, open source et auto-hébergé.**
> Il vit sur vos machines, vous connaît, vous parle au fil de la journée, agit pour vous sous contrôle, et continue de travailler quand vous n'êtes pas là.

| | |
|---|---|
| **Statut** | Développement initial — phase 0. Ce document décrit la cible ; certaines parties ne sont pas encore implémentées. |
| **Déploiement** | Docker Compose, un profil par machine |
| **Intelligence** | Modèles locaux (llama.cpp, Qwen), renfort cloud optionnel et plafonné |
| **Données** | Restent sur vos machines |
| **Licence** | À définir (MIT ou Apache-2.0 recommandées) |

---

## En bref

- **Des agents persistants** : des Bots nommés, avec un rôle, une mémoire et des outils, qui travaillent en arrière-plan et reprennent après un crash.
- **Une seule présence** : un « chief » parle au nom de tous les Bots, par messagerie, e-mail, voix, et un jour visio.
- **Local d'abord** : tourne sur du matériel modeste (GPU 4 Go, laptops, vieux smartphones) avec des modèles MoE en offload ; le cloud n'intervient qu'en renfort ponctuel et plafonné.
- **Confiance par conception** : gardien indépendant, approbations, instantanés avant toute action destructrice, coffre à secrets, connecteurs à portée limitée.
- **Une mémoire qui se construit** : un onboarding conversationnel peuple une arborescence de fichiers lisibles, corrigeables et supprimables.
- **Installation simple** : tout est dockerisé, `docker compose up` suffit.

---

## Sommaire

1. [Vision et principes](#1-vision-et-principes)
2. [Fonctionnalités](#2-fonctionnalités)
3. [Architecture](#3-architecture)
4. [Modèles et routage](#4-modèles-et-routage)
5. [Mémoire et onboarding](#5-mémoire-et-onboarding)
6. [Sécurité et confiance](#6-sécurité-et-confiance)
7. [Outils, connecteurs et canaux](#7-outils-connecteurs-et-canaux)
8. [Un compagnon vivant](#8-un-compagnon-vivant)
9. [Infrastructure et déploiement](#9-infrastructure-et-déploiement)
10. [Stack technique et structure du dépôt](#10-stack-technique-et-structure-du-dépôt)
11. [Roadmap](#11-roadmap)
12. [Risques et non-objectifs](#12-risques-et-non-objectifs)
13. [Démarrage](#13-démarrage)

---

## 1. Vision et principes

### Pourquoi DindonBot

Les agents personnels persistants comme Grok Bot ou Muse (Meta) montrent la direction : un ordinateur dédié dans le cloud, connecté à vos comptes, qui exécute de vrai travail et continue quand vous fermez l'application. Le prix à payer : vos données et vos accès passent par un tiers, l'usage est facturé ou limité, et vous ne maîtrisez ni l'environnement d'exécution ni les règles.

DindonBot propose la même catégorie de produit, **inversée** :

| | Agents cloud (Grok Bot, Muse…) | DindonBot |
|---|---|---|
| Hébergement | Cloud du fournisseur | Vos machines |
| Données | Chez le fournisseur | Chez vous |
| Modèles | Propriétaires | Vos modèles locaux, cloud en option |
| Coût | Abonnement ou usage | Matériel et électricité, cloud plafonné si activé |
| Personnalisation | Réglages proposés | Totale : code, prompts, outils, règles |
| Transparence | Limitée | Logs, mémoire et décisions lisibles |

*Comparatif indicatif, établi sur les annonces publiques à la date de rédaction.*

L'inspiration culturelle est assumée : celle du film *Her*, c'est-à-dire un compagnon logiciel qui vous connaît, vous parle, et vit dans votre environnement. L'ambition technique, elle, reste concrète : un runtime d'agents fiable sur du matériel de milieu de gamme.

### Principes

1. **Local d'abord, cloud en renfort.** L'objectif est d'extraire un maximum d'intelligence des modèles locaux. Un modèle cloud économique peut être appelé ponctuellement, jamais par défaut.
2. **Le LLM est une ressource interchangeable.** Le centre du système est le runtime : tâches, mémoire, outils, nœuds.
3. **Petit, lisible, hackable.** Pas de framework géant ; chaque module se lit en une soirée.
4. **Conçu pour des modèles modestes.** Checkpoints, vérification, réessais et routage compensent les erreurs au lieu de supposer un modèle parfait.
5. **L'autonomie se mérite.** Les droits augmentent par outil et par agent, jamais globalement.
6. **Le contrôle ne dépend pas de l'agent.** Un gardien indépendant valide les actions sensibles.
7. **Tout est inspectable.** Logs, tâches, mémoire et décisions du routeur sont des fichiers ou des tables lisibles.
8. **Dockerisé de bout en bout.** Installation, mise à jour et isolation passent par des conteneurs.
9. **MVP minuscule, évolution progressive.** Un agent qui marche avant tout le reste.

---

## 2. Fonctionnalités

### Les Bots

Chaque Bot est une entité nommée : rôle, outils autorisés, mémoire, modèle préféré.

| Bot | Rôle |
|---|---|
| `chief` | Orchestrateur et seule voix visible : reçoit les demandes, délègue, résume |
| `research` | Veille : nouveaux modèles et GGUF, releases, agents locaux, matériel IA |
| `code` | Travail sur les dépôts : tests, diagnostic, patchs proposés |
| `ops` | Santé des machines : serveurs d'inférence, disque, températures, services |
| `night` | Tâches longues de nuit : benchmarks, indexation, refactors autorisés |
| `scribe` | Notes, idées, journal de bord, résumés de journée |
| `coach` | Suivi des projets personnels : repère ceux qui stagnent, aide à définir « assez fini » |

Tout Bot tourne en arrière-plan, reprend après un crash, peut déléguer à un autre Bot, demande l'autorisation pour les actions sensibles et apprend des routines qu'on lui montre.

### Fonctions signature

| Fonction | Description |
|---|---|
| **Night Mode** | Les tâches longues continuent pendant l'absence ; au retour, un compte rendu clair des réussites et des approbations en attente |
| **Teach by Doing** | Un workflow montré une fois devient une *Skill* paramétrable et rejouable |
| **Suggestions d'automatisation** | Une action répétée plusieurs fois déclenche la proposition : « je l'automatise ? » |
| **Radar** | Des watchers (modèles, projets, news, marchés) qui décident ce qui mérite une interruption |
| **Auto-vérification** | Après chaque tâche : vérification, réparation si échec, puis rapport |
| **Briefing du jour** | État des machines, événements de la nuit, tâches en attente, une ou deux priorités |
| **Maintenance du cluster** | Détection d'un serveur d'inférence en panne, redémarrage, rapport de cause |
| **Capture d'idées** | Depuis le téléphone, une idée atterrit dans la mémoire du bon projet |
| **Vision à la demande** | Photo d'un courrier, d'un écran ou d'un objet analysée par un modèle de vision local |
| **Téléphones recyclés** | Les anciens smartphones deviennent des nœuds : caméra, capteur, serveur de notifications |

---

## 3. Architecture

```
                            DINDONBOT
                                │
      ┌─────────────────────────┼─────────────────────────┐
      │                         │                         │
  INTERFACES                 RUNTIME                    NODES
      │                         │                         │
  CLI · Voix · Web         Agents · Tasks            Serveur principal
  Messagerie · E-mail      Mémoire · Skills          Nœud lab
  Client Android           Router · Gardien          Clients · Téléphones
      │                         │                         │
      └─────────────────────────┼─────────────────────────┘
                                │
                           BACKENDS LLM
                    llama-server (Qwen MoE…)
                                │
                  (option) cloud à la demande
```

### Parcours d'une demande

```
Utilisateur (voix, texte, téléphone)
        │
        ▼
   Interface ──► Event (bus)
                    │
                    ▼
              Chief (routage)
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
     research      code         ops
        └───► Task Engine (checkpoints, reprise) ◄───┘
                    │
        Model Router → modèle + nœud
                    │
              Tool call proposé
                    │
              GARDIEN (autorise, demande, refuse)
                    │
              Exécution en sandbox
                    │
        Artifact + Mémoire + Réponse du chief
```

### Core et applications

- **DindonBot Core** : runtime, kernel, protocole inter-nœuds. Aucune logique propre à un projet.
- **Applications** au-dessus : assistant personnel, veille, agents de projets. Elles utilisent le core, jamais l'inverse.

### Le kernel : dix primitives

Le kernel doit être **parfait avant tout ajout**. Dix concepts, pas un de plus :

| Primitive | Rôle |
|---|---|
| **Agent** | Entité nommée : rôle, prompt système, outils autorisés, mémoire, modèle préféré |
| **Task** | Travail persistant : objectif, état, checkpoints, résultat, erreurs, reprise |
| **Tool** | Capacité appelable avec schéma, permissions et niveau de risque |
| **Memory** | Stockage durable à quatre couches (section 5) |
| **Model** | Description d'un modèle : fichier, taille, contexte, capacités, performances mesurées |
| **Node** | Machine ou appareil : ressources, modèles hébergés, outils, état de santé |
| **Skill** | Routine réutilisable apprise ou écrite : étapes, paramètres, vérifications |
| **Event** | Message sur le bus : voix détectée, fichier modifié, cron, nouveau modèle, crash |
| **Approval** | Demande de validation humaine : contexte, risque, réponse, expiration |
| **Artifact** | Sortie durable d'une tâche : rapport, patch, benchmark, avec provenance |

Règle de conception : une nouvelle fonctionnalité qui ne se décrit pas avec ces dix primitives doit justifier son existence.

---

## 4. Modèles et routage

### Hiérarchie des modèles

| Niveau | Modèles | Usage |
|---|---|---|
| **Local rapide** | Petit modèle dense (3B à 8B) | Réponses vocales, tri, classification, résumés courts |
| **Local principal** | Qwen 3.6 35B MoE en offload, environ 20 à 25 tokens/s sur le matériel de référence | Défaut pour presque tout : code, recherche, agents, tâches de nuit |
| **Cloud à la demande** | DeepSeek V4 Flash ou autre modèle économique | Tâches exigeant plus d'intelligence ou de vitesse que le local |

Le local est le choix par défaut. Le cloud est un renfort, pas une béquille.

### Le Model Router

Le router choisit **quel modèle, sur quel nœud ou chez quel fournisseur**, selon le type de tâche, le contexte requis, la latence acceptable (courte en vocal, illimitée la nuit), la charge des nœuds, les performances mesurées et la politique cloud.

```yaml
routes:
  voice_reply:    { prefer: small-fast,     max_latency_s: 2, node: main }
  code_task:      { prefer: qwen36-35b-moe, node: [lab, main] }
  deep_reasoning: { prefer: qwen36-35b-moe, node: main, allow_slow: true,
                    escalate_to: cloud-cheap }   # si la vérification échoue 2 fois
  night_batch:    { prefer: qwen36-35b-moe, node: main, schedule: night }
  summarize:      { prefer: small-fast,     node: any }
fallback: [main/qwen36-35b-moe, lab/qwen36-35b-moe]

cloud:
  enabled: true
  providers:
    cloud-cheap: { model: deepseek-v4-flash, api_key_env: DEEPSEEK_API_KEY }
  monthly_budget_eur: 5                  # plafond dur, au-delà : local uniquement
  allow_data: [public, project_code]     # jamais mémoire personnelle ni identité
  require_approval: first_use_per_task
```

### Règles du cloud

- **Opt-in** par route ou par tâche : rien ne sort sans règle explicite.
- **Escalade après échec** : cas typique, le modèle local échoue à la vérification et la tâche est retentée une fois en cloud.
- **Plafond de budget dur** par mois, compteur visible (`dindon cloud usage`).
- **Filtre de données** : mémoire personnelle, identité et secrets ne sortent jamais.
- **Journal complet** : tâche, modèle, tokens, coût et raison de chaque escalade.
- **Mesure de l'utilité** : le Model Lab compare local et cloud pour que le recours au cloud diminue avec le temps.

### Model Lab et tâches de référence

Le **Model Lab** mesure les modèles sur le matériel réel :

- vitesse de génération et de traitement du prompt ;
- consommation RAM/VRAM et **paramètres d'offload optimaux** par machine ;
- fiabilité du tool calling ;
- qualité sur un jeu de tâches de code, d'extraction et de résumé ;
- tenue sur contexte long.

Les **tâches de référence** (*golden tasks*) renforcent cette approche : toute tâche réelle réussie peut être promue en test rejouable, avec des vérifications déterministes (tests qui passent, fichier produit, format respecté) plutôt qu'un simple jugement par LLM.

```bash
dindon task promote <id>          # transforme une tâche réussie en test de référence
dindon lab regress <modèle>       # rejoue la suite sur un nouveau GGUF ou un nouveau prompt
```

Quand un nouveau modèle sort, le Lab indique s'il fait mieux ou moins bien **sur les tâches réelles de l'utilisateur**, et le router s'ajuste à partir de ces résultats.

---

## 5. Mémoire et onboarding

### Les quatre types de mémoire

| Type | Contenu | Exemple |
|---|---|---|
| **Sémantique** | Faits durables sur l'utilisateur et son environnement | « Le serveur principal héberge le modèle local » |
| **Épisodique** | Ce qui s'est passé, daté | « Hier, le benchmark a échoué faute de RAM » |
| **Procédurale** | Comment faire : skills et routines | « Procédure de redémarrage du serveur d'inférence » |
| **Projet** | État vivant de chaque projet | Dernière release, tests en échec, prochaine étape |

Stockage : **SQLite et fichiers Markdown/JSON** lisibles, embeddings locaux optionnels. La mémoire de projet permet de répondre à « on en était où hier avec le truc de Qwen ? » en reconstruisant l'état de l'activité, pas seulement l'historique de la discussion.

### Onboarding conversationnel

Dindon ne démarre pas à froid : à l'installation, il **fait connaissance** par une conversation, pas par un formulaire.

**Première session (10 à 15 minutes), en voix ou en texte :**

- comment s'appeler mutuellement, langue et ton souhaités ;
- machines disponibles et rôles (détection automatique quand c'est possible) ;
- centres d'intérêt, goûts, sujets de discussion ;
- rythme de la journée et moments à ne pas déranger ;
- attentes principales : veille, admin, code, compagnie ;
- canaux préférés et fréquence souhaitée ;
- **personnalité** : nom, ton, humour, degré de formalité, niveau d'initiative.

La session se termine par un **résumé de ce que Dindon a compris**, que l'utilisateur corrige. La mémoire initiale et la configuration des Bots en sont générées.

**Onboarding progressif :** le reste se fait au fil du temps, par petites questions au bon moment (« tu as parlé du japonais, je suis tes révisions ? »).

- reprenable et interruptible (tâche persistante avec checkpoints) ;
- chaque question est facultative et sautable ;
- une ou deux questions de découverte par jour au maximum ;
- auto-configuration selon le matériel détecté (modèle, offload, Bots activés).

### Arborescence de mémoire

```
memory/
├── about-you/          # identité, langue, ton, façon de communiquer
├── routines/           # habitudes, rythmes de la journée
├── interests/          # goûts, passions, sujets de discussion
├── projects/           # un fichier par projet vivant
├── people/             # entourage (avec précaution)
├── machines/           # parc matériel, nœuds, modèles, réglages
├── world/              # sujets suivis, sources de veille
├── conversations/      # moments marquants, running jokes, souvenirs partagés
└── dindon/             # réglages, permissions, historique des décisions
```

Règles :

- **Provenance sur chaque fait** : *dit par l'utilisateur*, *observé* ou *déduit*. Les déductions restent marquées comme telles.
- **Accès par rôle** : chaque Bot ne lit que les branches utiles à sa mission, ce qui protège la vie privée et réduit le contexte.
- **Liens entre fichiers** (`[[projet-x]]`, `[[machine-principale]]`) pour reconstruire un contexte complet.
- **Lisible, éditable, supprimable** dans un simple éditeur de texte.
- **Catégories sensibles en opt-in** : santé, finances, famille et relations ne sont explorées que sur demande, avec la question explicite « voulez-vous que je retienne ça ? ».
- **Droit à l'oubli simple** : une phrase ou une commande supprime un souvenir ou une branche.
- **Sauvegarde et export** automatiques vers un second nœud, exportables en un fichier.

### Consolidation nocturne

Pendant la nuit, un job de consolidation entretient la mémoire :

- fusion des doublons et détection des contradictions ;
- résumé des épisodes récents en faits durables ;
- mise à jour de l'état des projets ;
- liste de **propositions** de nettoyage soumise à l'utilisateur, jamais de suppression silencieuse.

### Identité

```
identity/
├── soul.md            # caractère, humour, façon de parler
├── values.md          # priorités, limites
├── communication.md   # concision, ton, règles d'interruption
└── preferences.md     # préférences apprises
```

Une personnalité stable et cohérente, définie par des fichiers éditables, pas une imitation d'humain.

---

## 6. Sécurité et confiance

La sécurité est une **défense en profondeur** : aucune couche n'est supposée infaillible, notamment parce que les agents locaux se trompent plus souvent et peuvent être manipulés par le contenu qu'ils lisent.

### Niveaux de risque

| Niveau | Exemples | Politique |
|---|---|---|
| Lecture | lire un fichier, `git status`, recherche | Automatique |
| Écriture locale | modifier un fichier du workspace, commit local | Automatique dans le workspace de l'agent, sinon demande |
| Impact | installer un paquet, lancer un service, écrire hors workspace | **Approbation** |
| Irréversible ou externe | suppression large, `git push`, envoi de message, paiement | **Approbation explicite à chaque fois** |

### Le Gardien

Le **Gardien** est un service **séparé de l'agent qui agit**, dans son propre conteneur et son propre réseau. Inspiré du principe d'agent de surveillance isolé adopté par les agents cloud, il applique cette idée en local :

- chaque appel d'outil proposé par un agent lui est soumis avant exécution ;
- il décide : **autoriser, demander l'approbation, refuser** ;
- il applique des règles déclaratives (YAML) et, en phase ultérieure, une revue d'anomalies par un petit modèle (volume inhabituel, destination inattendue, séquence suspecte) ;
- il **ne lit jamais** le contenu non fiable (pages web, e-mails) comme instructions : il ne voit que l'action proposée ;
- il détient le **coupe-circuit** (`dindon stop`) qui suspend tous les agents ;
- il est le seul à pouvoir émettre une approbation valide ; l'agent ne peut pas s'auto-approuver.

### Annulation et instantanés

Des agents ont déjà supprimé des données par accident dans des produits grand public. DindonBot prévoit donc :

- un **instantané automatique** du workspace (copie légère, git, ou instantané natif btrfs/ZFS) avant toute action destructrice ;
- un **mode simulation** (*dry-run*) pour les actions en masse ;
- un **journal d'annulation** : `dindon undo <action>` restaure l'état précédent tant que l'instantané existe ;
- une rétention configurable des instantanés.

### Coffre à secrets

Les identifiants, clés et moyens de paiement ne sont **jamais dans le contexte du modèle**.

- un coffre chiffré local (fichier chiffré ou gestionnaire auto-hébergé) ;
- un **courtier d'identifiants** : l'outil demande « utiliser l'identifiant X sur le domaine Y », et la couche d'outils l'injecte dans la requête ou la session navigateur ;
- portée limitée par Bot et par domaine ;
- masquage systématique des secrets dans les logs ;
- tout usage sensible (paiement, changement de mot de passe) exige une approbation explicite.

### Observer et reprendre la main

Le conteneur navigateur expose une **vue en direct** dans l'interface web (type noVNC) :

- l'utilisateur regarde l'agent travailler ;
- boutons **pause**, **reprendre la main** et **rendre la main**, utiles pour un CAPTCHA ou une double authentification ;
- enregistrement court des sessions pour l'audit et le rejeu, avec rétention limitée.

### Isolation

- **sandbox** : tout outil d'exécution (shell, fichiers, git, navigateur) tourne dans un conteneur dédié, système de fichiers en lecture seule, capacités retirées, réseau restreint ;
- **workspaces isolés** par agent, chemins explicitement autorisés ;
- **contenu externe = donnée, jamais instruction** (défense contre l'injection de prompt) ;
- **journal d'audit** de chaque action, décision du Gardien et appel cloud ;
- approbations par voix, CLI, web ou notification, avec **expiration**.

---

## 7. Outils, connecteurs et canaux

### Outils

| Phase | Outils |
|---|---|
| MVP | `shell`, `read_file`, `write_file`, `edit_file`, `list_dir`, `search`, `git`, `http`, `memory`, `delegate` |
| Suivants | `browser`, `notify`, `node_exec`, `model_server` |
| Plus tard | `screen` (capture d'écran locale, sur activation explicite), `vision` |

### Connecteurs

Les services externes (agenda, e-mail, fichiers, forges de code) passent par une couche de **connecteurs**, en s'appuyant sur le standard **MCP** :

- chaque connecteur déclare ses **portées** (lecture, écriture) dans un manifeste ;
- l'utilisateur les active **un par un**, avec une liste d'autorisation par Bot ;
- chaque connecteur tourne dans son propre conteneur, avec un accès réseau restreint ;
- **le navigateur sert de solution de repli** quand aucune API n'existe, avec les mêmes règles de Gardien et de coffre.

### Interfaces

- **CLI** : `dindon chat chief`, `dindon task list`, `dindon approve`.
- **Web** : interface FastAPI et HTMX (tâches, approbations, nœuds, mémoire, vue navigateur).
- **Voix** : module `voice/` (détection de parole, STT local, wake word, interruption, TTS en streaming). L'audio étant délicat dans un conteneur, il tourne en **client léger côté hôte** et dialogue avec le serveur par API.
- **Téléphone** : client léger Android (Termux ou application web) pour parler, noter une idée, recevoir des alertes et valider des approbations.

### Canaux de communication

| Canal | Usage | Phase |
|---|---|---|
| Messagerie instantanée | Conversation quotidienne, approbations, photos, notes vocales | 3 |
| Voix | Conversation mains libres | 3 |
| Notifications téléphone | Alertes et approbations | 4 |
| E-mail | Résumés longs, rapports, documents à relire | 4 |
| Visio avec avatar | Présence incarnée | 6, selon le matériel |

Pour la messagerie, plusieurs options sont à évaluer : **Matrix** (auto-hébergeable, cohérent avec la philosophie du projet), un **bot Telegram** (le plus rapide pour le MVP), Signal, ou WhatsApp. Une couche `channels/` abstrait le canal : un Bot écrit un message, la couche choisit comment le livrer.

### Boîte aux lettres de Dindon

Dindon peut disposer de **sa propre adresse** (alias ou boîte dédiée) pour recevoir des messages transférés ou être mis en copie d'un échange. Il existe aussi une action « Partager vers Dindon » depuis Android. Tout message entrant est traité comme **contenu non fiable**, avec une liste d'expéditeurs autorisés.

---

## 8. Un compagnon vivant

L'objectif n'est pas un outil qu'on ouvre au besoin, mais **une présence qui vit avec l'utilisateur** : un assistant qui le connaît, lui parle dans la journée, et avec lequel on peut aussi simplement discuter pour se distraire.

### Il prend l'initiative, avec mesure

- message du matin (briefing court) et récap du soir ;
- compte rendu d'une tâche terminée, trouvaille liée aux centres d'intérêt ;
- rappel au bon moment ;
- question de curiosité ou « comment s'est passée ta journée ? » quand c'est approprié ;
- suggestion d'automatisation après une action répétée.

### Un seul Dindon, plusieurs agents

Le `chief` est la **seule voix visible**. Les autres Bots lui remettent leurs résultats, et il décide quoi dire, quand, et comment. L'utilisateur ne reçoit jamais cinq messages de cinq Bots.

### Moteur d'initiative

Pour être vivant sans être envahissant :

- **heures calmes** et jours sans notification, appris depuis l'onboarding ;
- **score d'importance** par événement : urgent (interrompt), utile (attend un bon moment), anecdotique (récap du soir) ;
- **plafond quotidien** de messages proactifs ;
- **apprentissage du retour** : un message ignoré ou un « pas maintenant » ajuste le comportement ;
- **respect du contexte** : pas de message pendant une réunion, un appel ou une session de concentration détectée.

### Discussion et compagnie

Dindon peut discuter : humour, débats, idées, coups de cœur. Pour que ce soit crédible, il s'appuie sur la mémoire conversationnelle (`conversations/`), l'identité (`soul.md`), la connaissance de la vie de l'utilisateur et le choix du bon moment.

### Garde-fous relationnels

Un compagnon réellement présent exige des règles posées dès la conception :

- **Transparence** : Dindon est une IA, ne prétend jamais le contraire, ne simule pas de sentiments qu'il n'a pas et ne revendique pas de relation exclusive.
- **Il encourage les liens humains** : il peut rappeler d'appeler un proche, de sortir, de proposer quelque chose à des amis, et ne cherche jamais à retenir l'utilisateur.
- **Aucune manipulation d'engagement** : pas de relance culpabilisante, pas de « tristesse » mise en scène lors d'une absence.
- **Honnêteté d'abord** : il dit quand il n'est pas d'accord, quand une erreur est probable, quand un plan est risqué.
- **Contrôle total** : fréquence, tonalité et intimité sont réglables, et tout peut être coupé.
- **Attention au bien-être** : si l'utilisateur semble aller mal, Dindon le remarque et l'oriente vers de vraies personnes et de vraies ressources, sans jouer au thérapeute.

### Le Bot `coach`

Pour ceux qui lancent beaucoup de projets et en finissent peu, un Bot **optionnel** suit l'état des projets, repère ceux qui stagnent, aide à définir un critère d'achèvement réaliste et propose de réduire le périmètre. Ton direct, bienveillant, jamais culpabilisant.

---

## 9. Infrastructure et déploiement

### Configuration de référence de l'auteur

| Machine | Spécifications | Rôle |
|---|---|---|
| **PC de bureau toujours allumé** | Ryzen 5 1600AF, 32 Go de RAM, GTX 1650 Super 4 Go | **Serveur principal** : daemon, scheduler, mémoire, tâches de nuit, serveur d'inférence |
| **Laptop de développement** | Acer Nitro 5, Ryzen 5 8300H, 16 Go de RAM, GTX 1050 4 Go | **Nœud lab** : développement, benchmarks, second serveur d'inférence, sauvegarde de la mémoire |
| **Laptop du quotidien** | Windows 11, Ryzen 5 5500U, 20 Go de RAM | **Client** : interface web, voix, navigation ; peu ou pas d'inférence |
| **Smartphone principal** | Android | **Compagnon de poche** : micro, notifications, capture d'idées |
| **Quatre smartphones en stock** | Android milieu de gamme | **Nœuds capteurs** : caméra, watcher, notifications |

### Contraintes matérielles assumées

- **4 Go de VRAM : le MoE avec offload est la bonne stratégie.** Qwen 3.6 35B MoE n'active que peu de paramètres par token : les experts restent en RAM, le GPU prend le reste. Avec les bons paramètres, la mesure atteint environ 20 à 25 tokens/s sur les deux PC de développement.
- **Les paramètres d'offload sont une ressource de premier ordre** : couches ou experts sur GPU, taille de contexte, quantification, threads. Ils vivent dans la configuration de chaque nœud et sont suivis par le Model Lab.
- **Le goulot est la RAM et sa bande passante**, pas seulement la VRAM.
- **Une inférence à la fois par GPU.** Le scheduler sérialise ; le parallélisme se fait entre nœuds.
- **Le serveur principal est un point de défaillance** : sauvegarde automatique vers un second nœud et **mode dégradé explicite** côté clients.
- **Les téléphones** sont des périphériques, pas des serveurs d'inférence.

### Paliers matériels

Le projet grandit avec le matériel sans réécriture : LLM, voix et image sont des ressources interchangeables.

| Palier | Matériel | Ce qui devient possible |
|---|---|---|
| **0 — Aujourd'hui** | GPU 4 Go, MoE en offload | Agents, messagerie, voix en streaming, tâches de nuit, escalade cloud ponctuelle |
| **1 — Machine IA dédiée** | Gros GPU ou PC orienté IA (type laptops RTX Spark annoncés) | Modèles plus gros et rapides en local, moins de cloud, vision locale, voix plus riche |
| **2 — Présence incarnée** | Palier 1 avec marge de calcul temps réel | Visio avec avatar, voix expressive |

Les spécifications des machines du palier 1 sont à confirmer à l'achat ; le Model Lab mesurera leur apport réel.

### Tout est dockerisé

Tout DindonBot tourne dans Docker : **installation en un `docker compose up`, mise à jour en un `docker compose pull`**, même base sur chaque machine avec un profil différent.

- **Simple** : pas de dépendance Python, CUDA ou navigateur à installer sur l'hôte.
- **Maintenable** : mise à jour par image, retour arrière en changeant le tag.
- **Reproductible** : serveur principal, nœud lab et machine d'un futur utilisateur partent de la même définition.
- **Sûr** : outils et connecteurs tournent dans des conteneurs isolés.
- **Portable** : migrer vers une machine plus puissante revient à copier `data/` et relancer le compose.

| Service | Rôle | Où |
|---|---|---|
| `dindon` | Daemon : agents, Task Engine, scheduler, mémoire, router | Serveur principal |
| `guardian` | Gardien : validation des actions, approbations, coupe-circuit | Serveur principal |
| `web` | API et interface web | Serveur principal |
| `llama` | `llama-server` avec le modèle local (GPU) | Serveur principal et nœud lab |
| `sandbox` | Exécution isolée des outils | Chaque nœud |
| `browser` | Navigateur headless avec vue en direct | Optionnel |
| `channels` | Passerelles de messagerie et e-mail | Serveur principal |
| `backup` | Sauvegarde périodique de `data/` vers un autre nœud | Serveur principal |

Profils Compose : `core` (serveur principal), `llm` (nœud d'inférence), `browser` (navigation).

**Exemple de `docker-compose.yml` (à adapter) :**

```yaml
services:
  dindon:
    image: ghcr.io/nikodindon/dindonbot:latest
    profiles: [core]
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/data               # mémoire, SQLite, artefacts
      - ./bots:/app/bots:ro
      - ./identity:/app/identity
    depends_on: [llama, sandbox, guardian]
    healthcheck:
      test: ["CMD", "dindon", "doctor", "--quick"]
      interval: 60s

  guardian:
    image: ghcr.io/nikodindon/dindonbot-guardian:latest
    profiles: [core]
    restart: unless-stopped
    read_only: true
    volumes:
      - ./policy:/policy:ro        # règles du Gardien, en lecture seule
      - ./data/audit:/audit        # journal d'audit
    networks: [control]

  web:
    image: ghcr.io/nikodindon/dindonbot:latest
    command: dindon web
    profiles: [core]
    restart: unless-stopped
    ports: ["127.0.0.1:8700:8700"]   # accès distant via Tailscale uniquement
    volumes: ["./data:/data"]

  llama:
    image: ghcr.io/ggml-org/llama.cpp:server-cuda
    profiles: [llm, core]
    restart: unless-stopped
    environment:
      LLAMA_ARG_MODEL: /models/${MODEL_FILE}
      LLAMA_ARG_CTX_SIZE: ${CTX_SIZE:-32768}
      LLAMA_ARG_N_GPU_LAYERS: ${GPU_LAYERS:-99}
      # autres réglages d'offload MoE : variables LLAMA_ARG_* ou `command:`
    volumes: ["./models:/models:ro"]
    ports: ["8080:8080"]
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  sandbox:
    image: ghcr.io/nikodindon/dindonbot-sandbox:latest
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    volumes: ["./workspaces:/workspaces"]
    networks: [isolated]

  backup:
    image: ghcr.io/nikodindon/dindonbot-backup:latest
    profiles: [core]
    volumes: ["./data:/data:ro"]
    environment:
      BACKUP_TARGET: ${BACKUP_TARGET}   # ex. le nœud lab via Tailscale

networks:
  isolated:
    internal: true
  control:
    internal: true
```

*Les noms d'images sont des cibles de publication, pas des artefacts existants. Les variables d'offload de llama.cpp sont à vérifier selon la version utilisée.*

**Points à anticiper :**

- **GPU dans Docker** : pilotes NVIDIA et **NVIDIA Container Toolkit** requis sur chaque nœud Linux.
- **Audio** : micro et haut-parleurs sont plus simples côté hôte (client `voice/` léger).
- **Windows** : simple client via navigateur ou client léger, sans Docker nécessaire.
- **Téléphones** : clients légers, sans conteneur.
- **Données** : tout ce qui compte vit dans `data/` (SQLite, mémoire, artefacts) : un seul dossier à sauvegarder.
- **Modèles** : les GGUF restent sur l'hôte (`./models`), jamais dans les images.
- **Multi-nœuds** : un compose par machine avec son profil, découverte via Tailscale.

---

## 10. Stack technique et structure du dépôt

### Stack

| Couche | Choix |
|---|---|
| Langage | Python 3.11+ (typé, `asyncio`) |
| Inférence locale | `llama.cpp` / `llama-server` (API compatible OpenAI), Qwen 3.6 35B MoE en GGUF avec offload |
| Inférence cloud (option) | API compatible OpenAI, derrière le router, budget plafonné |
| Client LLM | Client maison (streaming SSE, réessais, découverte via `/v1/models`), même interface locale et cloud |
| Stockage | SQLite (tâches, mémoire, événements) et fichiers Markdown/YAML (identité, skills, config) |
| Bus d'événements | En processus au départ, puis HTTP/WebSocket entre nœuds |
| Réseau | Tailscale entre toutes les machines |
| Déploiement | Docker et Docker Compose (profils par nœud), NVIDIA Container Toolkit |
| Connecteurs | MCP |
| Navigateur | Playwright (headless), vue en direct |
| Voix | faster-whisper, Silero VAD, Kokoro, PortAudio (client léger côté hôte) |
| API et web | FastAPI et HTMX |
| Tests | pytest, **faux serveur LLM déterministe**, tests de reprise après crash, tâches de référence |
| Configuration | TOML et YAML |

### Structure du dépôt

```
dindonbot/
├── README.md
├── pyproject.toml
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── config.example.toml
├── dindon/
│   ├── kernel/          # les 10 primitives
│   ├── runtime/         # boucle d'agent, scheduler, daemon, compaction
│   ├── llm/             # client, router, model lab, tâches de référence
│   ├── tools/           # shell, fs, git, http, browser, notify
│   ├── connectors/      # couche MCP, manifestes de portées
│   ├── memory/          # SQLite, embeddings, compaction, consolidation
│   ├── nodes/           # protocole inter-machines, découverte, santé
│   ├── interfaces/      # cli, web, voice, phone
│   ├── channels/        # messagerie, e-mail, notifications
│   ├── watchers/        # radar modèles, projets, news, marchés
│   ├── onboarding/      # conversation de découverte
│   ├── companion/       # moteur d'initiative, identité
│   └── safety/          # gardien, coffre à secrets, instantanés, audit
├── bots/                # définitions YAML des agents
├── identity/            # soul.md, values.md, communication.md
├── memory/              # arborescence générée à l'onboarding
├── policy/              # règles du gardien
├── skills/              # routines apprises
├── docs/
│   ├── kernel.md        # spécification des 10 primitives
│   ├── security.md      # modèle de menace, gardien, coffre
│   └── nodes.md
└── tests/
    ├── fake_llm/        # serveur LLM factice
    ├── golden/          # tâches de référence
    └── e2e/
```

---

## 11. Roadmap

Quatre générations : **Assistant, Agent, Équipe, Compagnon**. Chaque phase produit quelque chose d'utilisable le jour même, et la suivante ne s'ouvre que lorsque son critère de sortie est atteint.

### Phase 0 — Kernel et MVP (2 à 3 semaines)

*Un agent, un nœud, un vrai travail utile, un état qui ne se perd pas.*

- [ ] Dépôt, `pyproject.toml`, CI, faux serveur LLM
- [ ] **Dockerisation dès le départ** : `Dockerfile`, `docker-compose.yml` (dindon, llama, sandbox), `.env.example`
- [ ] Spécification écrite des dix primitives (`docs/kernel.md`) et du modèle de menace (`docs/security.md`) **avant** le code
- [ ] Client LLM compatible OpenAI branché sur Qwen 3.6 35B MoE
- [ ] Boucle d'agent avec `shell`, `read/write/edit_file`, `list_dir`, `git`
- [ ] **Task Engine** : tâches en SQLite, checkpoints, reprise
- [ ] Approbations minimales en CLI
- [ ] CLI : `dindon chat`, `dindon task run/list/resume`
- [ ] Premier cas d'usage réel : « lance les tests de mon dépôt et résume les échecs »

**Critère de sortie :** le processus est tué en pleine tâche, relancé, et la tâche reprend.

### Phase 1 — Bots, mémoire et sécurité de base (3 à 4 semaines)

- [ ] Bots en YAML (rôle, outils, permissions, modèle) ; workspaces isolés
- [ ] Mémoire à quatre couches, **provenance des faits**, accès par rôle, compaction
- [ ] Daemon conteneurisé (`restart: unless-stopped`, healthcheck), journal d'audit lisible
- [ ] Bots `chief` et `code`
- [ ] **Onboarding v1** (texte) : première session, génération de l'arborescence mémoire, résumé corrigeable
- [ ] Briefing du jour (texte)
- [ ] **Gardien v1** : service séparé, règles YAML, autoriser/demander/refuser, coupe-circuit
- [ ] **Instantanés avant action destructrice** et `dindon undo`

**Critère de sortie :** un briefing utile chaque matin, et une action destructrice est bloquée puis annulable.

### Phase 2 — Multi-nœuds et routage (3 à 4 semaines)

- [ ] Protocole de nœud (enregistrement, santé, capacités) via Tailscale ; rôles serveur, lab, client
- [ ] `model_server` : démarrage et arrêt à distance du serveur d'inférence
- [ ] **Model Router** à politiques YAML avec repli
- [ ] **Backend cloud optionnel** : escalade après échec, budget plafonné, filtre de données, journal des coûts
- [ ] **Model Lab v1** : vitesse, mémoire, tool calling, offload ; comparaison local contre cloud
- [ ] **Tâches de référence** : `dindon task promote`, `dindon lab regress`
- [ ] **Coffre à secrets v1** et courtier d'identifiants
- [ ] Bot `ops`, profils Compose par nœud, service `backup`
- [ ] Scheduler qui sérialise l'accès aux GPU

**Critère de sortie :** un nouveau GGUF est testé, comparé sur les tâches de référence et routé automatiquement.

### Phase 3 — Voix, canaux et équipe (environ 1 mois)

- [ ] Module `voice/` : STT, TTS en streaming, wake word, interruption ; la voix pilote le chief ; approbations vocales
- [ ] Couche `channels/` et premier canal de messagerie (Telegram pour démarrer, Matrix à évaluer)
- [ ] Message du matin et récap du soir
- [ ] Délégation inter-agents et fils partagés ; Bots `research` et `scribe`
- [ ] **Watchers** : nouveaux modèles et GGUF, releases GitHub
- [ ] **Connecteurs MCP à portées** (lecture/écriture), activation un par un
- [ ] **Navigateur avec vue en direct** et prise de contrôle
- [ ] Interface web : tâches, approbations, nœuds, mémoire
- [ ] Onboarding vocal

**Critère de sortie :** une approbation validée depuis le téléphone et une conversation vocale avec le chief.

### Phase 4 — Autonomie encadrée (1 à 2 mois)

- [ ] **Night Mode** : tâches longues planifiées
- [ ] **Auto-vérification et réparation** ; évaluation automatique de la qualité
- [ ] **Teach by Doing** et **suggestions d'automatisation**
- [ ] **Consolidation nocturne de la mémoire**
- [ ] Sandbox renforcé
- [ ] **Gardien v2** : revue d'anomalies par un petit modèle
- [ ] Notifications et approbations sur téléphone
- [ ] Canal e-mail et **boîte aux lettres de Dindon**
- [ ] Onboarding progressif

**Critère de sortie :** une nuit de travail autonome, un compte rendu clair au réveil, aucune action sensible sans approbation.

### Phase 5 — Présence et personnalité (environ 2 mois)

- [ ] Identité appliquée à la voix et au style ; personnalité réglée à l'onboarding
- [ ] **Moteur d'initiative** : budget de messages, heures calmes, score d'importance, apprentissage du retour
- [ ] Chief comme voix unique
- [ ] Mémoire conversationnelle et mode discussion
- [ ] **Garde-fous relationnels** implémentés et testés
- [ ] Bot `coach` (suivi de projets)
- [ ] **Vision à la demande** (modèle de vision local)
- [ ] Outil `screen` sur activation explicite ; téléphones recyclés en nœuds capteurs

### Phase 6 — Ouverture (long terme)

- [ ] Dindon comme **serveur** : API compatible OpenAI et serveur MCP pour d'autres outils
- [ ] Export et import de Bots et de skills
- [ ] **Skill forge** : l'agent propose de nouvelles skills, les teste en sandbox, l'utilisateur relit le diff avant installation
- [ ] **Mode foyer** : plusieurs utilisateurs, mémoire privée par personne, espace partagé (courses, agenda)
- [ ] Onboarding multi-utilisateurs, installation en une commande avec détection du matériel
- [ ] Réseau de Dindons entre proches, sans serveur central
- [ ] Paliers matériels 1 et 2 : modèles plus gros, visio avec avatar
- [ ] Génération de Bots par intention ; amélioration des prompts et skills validée par le Model Lab

---

## 12. Risques et non-objectifs

### Risques

| Risque | Parade |
|---|---|
| **Démarrer dix choses, n'en finir aucune** | MVP volontairement minuscule ; critère de sortie par phase |
| **Sur-ingénierie du kernel** | Dix primitives maximum, tout ajout justifié par un besoin vécu |
| **Modèle local trop faible pour une tâche** | Vérification, réessais, petites étapes ; en dernier recours, escalade cloud plafonnée et journalisée |
| **Dérive vers le tout-cloud** | Budget dur, filtre de données, mesure local contre cloud |
| **Fuite de données vers le cloud** | Catégories autorisées explicitement ; mémoire personnelle, identité et secrets jamais envoyés |
| **Agent qui fait une bêtise** | Gardien indépendant, approbations, workspaces isolés, instantanés et annulation |
| **Injection de prompt** | Contenu externe traité comme donnée ; Gardien qui ne lit que l'action proposée ; outils sensibles soumis à approbation |
| **Fuite d'identifiants** | Coffre à secrets, courtier d'identifiants, secrets absents du contexte et des logs |
| **Notifications envahissantes** | Budget d'initiative, heures calmes, score d'importance, voix unique |
| **Attachement excessif** | Transparence sur la nature d'IA, encouragement des liens humains, aucune manipulation d'engagement |
| **Onboarding trop long ou intrusif** | Session courte, questions facultatives, catégories sensibles en opt-in |
| **Latence en vocal** | Petit modèle rapide pour la voix, gros modèle pour les tâches non interactives |
| **Dépendance au serveur principal** | État sauvegardable, mode dégradé explicite, nœud remplaçable |
| **Dispersion sur les interfaces** | CLI, puis messagerie, puis voix, puis le reste |

### Non-objectifs

- **Un marketplace public de Bots ou de skills** tant qu'un modèle de confiance (signature, revue, permissions déclarées) n'existe pas.
- **Une présence vidéo avec avatar** avant d'avoir le matériel qui la rend fluide.
- **Une autonomie sans approbation** sur les actions irréversibles ou externes.
- **Égaler les plus gros modèles cloud** : le projet cherche l'utilité sur du matériel modeste, pas le record de qualité.
- **Supporter tous les services de messagerie** : un ou deux canaux bien faits valent mieux que dix approximatifs.

**Règle d'or : chaque phase doit servir au quotidien avant de commencer la suivante.**

---

## 13. Démarrage

*Prérequis : Docker Engine avec Compose v2 ; pour le GPU, pilotes NVIDIA et NVIDIA Container Toolkit.*

```bash
git clone https://github.com/nikodindon/dindonbot.git
cd dindonbot

cp .env.example .env            # modèle, contexte, offload, clés optionnelles (cloud)
mkdir -p models && cp /chemin/vers/qwen-3.6-35b-moe.gguf models/

docker compose --profile core up -d
docker compose exec dindon dindon doctor          # nœuds, modèles, permissions
docker compose exec -it dindon dindon onboard     # première conversation de découverte

docker compose exec dindon dindon bot create code --role "Diagnostic et tests de mes dépôts"
docker compose exec dindon dindon task run code "lance les tests de mon dépôt et résume les échecs"
```

Sur un nœud d'inférence secondaire :

```bash
docker compose --profile llm up -d
```

Mise à jour : `docker compose pull && docker compose up -d`

---

## Philosophie

> DindonBot n'essaie pas d'être l'IA la plus intelligente du monde.
> Il essaie d'être **la plus utile possible sur votre matériel, avec vos modèles, selon vos règles**.

Pas de magie noire. Pas de cloud obligatoire. Du code qu'on comprend, sur des machines qu'on possède.

Et si tout se passe bien : un compagnon qui vous connaît, vous parle au fil de la journée, travaille pendant que vous dormez, et vous renvoie vers les vrais gens quand il le faut.

*« Un Dindon qui travaille pendant que tu dors. »* 🦃

---

**Auteur** : nikodindon · **Statut** : projet personnel et expérimental · **Licence** : à définir
