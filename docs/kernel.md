# Spécification du kernel

| | |
|---|---|
| **Version** | 0.1 (brouillon) |
| **Statut** | Brouillon évolutif ; les premières briques du kernel sont implémentées sous `dindon/kernel/` |
| **Portée** | Les dix primitives, leurs invariants, leurs états, leur persistance et leurs tests de conformité |
| **Hors portée** | Stratégies de prompt, politiques du router, canaux, moteur d'initiative, onboarding, voix, interface (voir [Hors du kernel](#9-hors-du-kernel)) |

## Sommaire

1. [Objectif et règles de conception](#1-objectif-et-règles-de-conception)
2. [Conventions](#2-conventions)
3. [Invariants du kernel](#3-invariants-du-kernel)
4. [Les dix primitives](#4-les-dix-primitives)
5. [Flux de référence](#5-flux-de-référence)
6. [Persistance](#6-persistance)
7. [Modèle d'erreurs](#7-modèle-derreurs)
8. [Tests de conformité](#8-tests-de-conformité)
9. [Hors du kernel](#9-hors-du-kernel)
10. [Périmètre par phase](#10-périmètre-par-phase)
11. [Questions ouvertes](#11-questions-ouvertes)

---

## 1. Objectif et règles de conception

Le kernel est la **plus petite couche** qui rend possible un agent persistant, fiable et contrôlable. Tout le reste (voix, messagerie, personnalité, onboarding) se construit dessus et peut changer sans le toucher.

Règles :

1. **Dix primitives, pas une de plus.** Une nouvelle notion doit se décrire avec les dix existantes, sinon elle doit justifier son entrée dans le kernel.
2. **Le kernel ne parle ni au réseau ni à un modèle directement.** Il définit des interfaces (`Model`, `Tool`, `Node`) ; les implémentations vivent hors du kernel.
3. **Déterministe par injection.** Horloge, générateur aléatoire et modèle sont injectés ; avec un faux LLM, une exécution se rejoue à l'identique.
4. **Tout état est persistant et reprenable.** Un kill brutal n'efface jamais le travail déjà fait.
5. **Tout changement d'état émet un événement.** Pas de transition silencieuse.
6. **Refus par défaut.** Aucun droit n'est implicite.

---

## 2. Conventions

- **Identifiants** : ULID préfixés par type (`agt_`, `tsk_`, `stp_`, `tcl_`, `mem_`, `mdl_`, `nod_`, `skl_`, `evt_`, `apr_`, `art_`). Triables par date, uniques entre nœuds.
- **Horodatages** : UTC, ISO 8601, précision milliseconde.
- **Sérialisation** : JSON ; chaque objet porte `schema_version` (entier).
- **Hachages** : SHA-256, encodés en hexadécimal.
- **Schémas ci-dessous** : notation de type Python (`dataclass`) ; les types `Ref[X]` sont des identifiants vers un objet `X`.
- **Mots-clés** : DOIT, NE DOIT PAS, DEVRAIT sont employés au sens de la RFC 2119.

---

## 3. Invariants du kernel

Ces invariants sont testés (section 8). Les violer est un bug du kernel, pas un choix de configuration.

| # | Invariant |
|---|---|
| **K1** | Un appel d'outil ne s'exécute **jamais** sans décision préalable du Gardien (`authorized`). |
| **K2** | Un agent **ne peut pas** créer, modifier ni valider une `Approval`, ni modifier ses propres permissions. |
| **K3** | Une `Approval` est **liée au hachage exact** de l'action approuvée : changer un argument l'invalide. |
| **K4** | Les secrets **ne figurent jamais** dans un objet du kernel, un événement, un artefact ou un contexte de modèle. Seuls des **noms** de secrets circulent. |
| **K5** | Le journal d'événements est **append-only**. |
| **K6** | Toute transition d'état d'une `Task`, d'un `ToolCall` ou d'une `Approval` émet un `Event` dans la **même transaction** que l'écriture d'état. |
| **K7** | Une `Task` interrompue (crash, arrêt) **reprend** depuis son dernier checkpoint sans rejouer d'effet de bord non idempotent. |
| **K8** | Toute entrée de `Memory` a une **provenance** ; une entrée `inferred` n'est jamais présentée comme `stated`. |
| **K9** | Un agent n'accède qu'aux branches de mémoire de son `memory_scope`. |
| **K10** | Un appel à un modèle `cloud` n'a lieu que si la politique l'autorise **pour la classe de données** concernée. |
| **K11** | Un `Artifact` est **immuable** ; sa modification crée un nouvel artefact. |
| **K12** | Le kernel s'exécute **sans réseau** et sans LLM réel (tests, rejeu). |

---

## 4. Les dix primitives

Vue d'ensemble des relations :

```
                    ┌─────────┐ appartient à ┌──────────┐
                    │  Agent  │◄─────────────│   Task   │──── produit ───► Artifact
                    └────┬────┘              └────┬─────┘
                         │ autorisé à             │ contient
                         ▼                        ▼
                    ┌─────────┐   soumis à   ┌──────────┐  nécessite   ┌──────────┐
                    │  Tool   │◄─────────────│ ToolCall │─────────────►│ Approval │
                    └────┬────┘              └────┬─────┘              └──────────┘
                         │ s'exécute sur          │ émet
                         ▼                        ▼
                    ┌─────────┐              ┌──────────┐
                    │  Node   │◄─ héberge ───│  Model   │         Event (tout émet)
                    └─────────┘              └──────────┘
                                   Memory ◄── lue/écrite par Agent (selon scope)
                                   Skill  ◄── invoquée par Agent, exécutée comme Task
```

### 4.1 Agent

Entité nommée qui exécute des tâches.

```python
@dataclass
class Agent:
    id: Ref[Agent]
    name: str                      # "chief", "code", "ops"…
    role: str                      # description courte
    system_prompt: str             # chemin vers un fichier de prompt
    tools: list[ToolGrant]         # liste blanche explicite (refus par défaut)
    memory_scope: MemoryScope      # branches lisibles et inscriptibles
    model_route: str               # clé de route du router, pas un modèle précis
    limits: Limits                 # max_steps, max_tokens, max_wallclock_s
    autonomy: int                  # 0 = tout demander … 3 = autonome sur le bas risque
    status: Literal["enabled", "disabled", "suspended"]
    schema_version: int

@dataclass
class ToolGrant:
    tool: str                      # nom d'outil
    scopes: list[str]              # ex. ["read"], ["write:/workspaces/code"]
    max_risk: Risk                 # plafond de risque pour cet agent
```

**Règles**
- Défini dans un fichier YAML versionné (`bots/<nom>.yaml`) ; chargé en lecture seule par le runtime.
- Un agent ne se modifie pas lui-même (K2) ; seule une action humaine change sa définition.
- `suspended` est positionné par le coupe-circuit du Gardien ; un agent suspendu ne démarre plus de tâche.
- `autonomy` ne relâche jamais les règles du Gardien pour les risques `impact` et `irreversible`.

### 4.2 Task

Unité de travail persistante et reprenable.

```python
@dataclass
class Task:
    id: Ref[Task]
    agent: Ref[Agent]
    parent: Ref[Task] | None       # tâche déléguée
    goal: str                      # objectif en langage naturel
    input: dict                    # paramètres structurés
    state: TaskState
    budget: Budget                 # tokens, étapes, durée, coût cloud
    verification: list[Check]      # contrôles de fin (voir ci-dessous)
    schedule: Schedule | None      # cron, déclencheur d'événement, fenêtre de nuit
    priority: int
    result: Ref[Artifact] | None
    error: ErrorInfo | None
    created_at: str
    updated_at: str
    schema_version: int
```

**États**

```
pending ─► running ─► verifying ─► succeeded
              │  ▲        │
              │  │        └──► failed (après réparations épuisées)
              ▼  │
   waiting_approval / waiting_input / paused
              │
   cancelled  /  failed  (depuis n'importe quel état actif)
```

| Transition | Déclencheur | Événement |
|---|---|---|
| `pending → running` | Scheduler | `task.started` |
| `running → waiting_approval` | Décision `ask` du Gardien | `task.waiting_approval` |
| `waiting_approval → running` | Approbation reçue | `task.resumed` |
| `running → waiting_input` | L'agent demande une précision à l'utilisateur | `task.waiting_input` |
| `running → paused` | Coupe-circuit ou action utilisateur | `task.paused` |
| `running → verifying` | L'agent déclare avoir terminé | `task.verifying` |
| `verifying → succeeded` | Tous les `Check` passent | `task.succeeded` |
| `verifying → running` | Un `Check` échoue et il reste du budget de réparation | `task.repairing` |
| `* → failed` | Erreur non récupérable ou budget épuisé | `task.failed` |
| `* → cancelled` | Action utilisateur | `task.cancelled` |

**Étapes et checkpoints**

Une tâche est une suite d'**étapes** (`Step`). Chaque étape est soit un appel au modèle, soit un appel d'outil. L'intention est persistée avant l'appel ; son résultat est ensuite enregistré comme checkpoint dans la même transaction que l'événement correspondant.

```python
@dataclass
class Step:
    id: Ref[Step]
    task: Ref[Task]
    index: int                     # ordre strict, sans trou
    kind: Literal["model", "tool"]
    state: Literal["intent", "done", "failed"]
    payload: dict                  # messages (ou référence) / ToolCall
    result: dict | None
    started_at: str
    finished_at: str | None
    tool_idempotent: bool | None   # copie de la propriété au moment de l'intention
    idempotency_key: str | None    # clé réutilisée lors d'une reprise d'outil
```

Le checkpoint d'intention DOIT être persisté avant l'appel externe. Le résultat et son événement DOIVENT ensuite être persistés dans la même transaction. Les payloads et résultats restent sans secret (K4).

**Reprise (K7)**

À la reprise, le runtime relit les étapes et applique :

| Dernière étape | Action |
|---|---|
| `done` | Continuer à l'étape suivante |
| `intent` d'un appel **modèle** | Relancer l'appel (sans effet de bord) |
| `intent` d'un outil **idempotent** | Relancer avec la même clé d'idempotence |
| `intent` d'un outil **non idempotent** | **Ne pas relancer.** Vérifier l'état réel via la sonde de l'outil ; si indéterminé, passer la tâche en `waiting_input` et demander à l'utilisateur. Tant que les sondes ne sont pas disponibles, la reprise demande toujours une intervention humaine. |

Ce principe, **écrire l'intention avant, le résultat après**, est la base de la fiabilité du kernel.

**Contrôles de fin (`Check`)**

```python
@dataclass
class Check:
    kind: Literal["command", "file_exists", "schema", "assertion", "model_review"]
    spec: dict                     # ex. {"cmd": "pytest -q", "expect_exit": 0}
    deterministic: bool
```

Les contrôles **déterministes** (`command`, `file_exists`, `schema`) sont préférés. `model_review` n'est qu'un complément et ne peut pas, seul, déclarer qu'une tâche à effet externe a réussi.

### 4.3 Tool

Capacité appelable, décrite de façon déclarative.

```python
@dataclass
class Tool:
    name: str
    version: str
    description: str
    input_schema: dict             # JSON Schema
    output_schema: dict | None
    risk: Risk                     # read | local_write | impact | irreversible
    idempotent: bool
    probe: str | None              # outil/commande qui vérifie l'état réel après interruption
    runs_in: Literal["sandbox", "host", "connector"]
    needs_secrets: list[str]       # noms uniquement (K4)
    timeout_s: int
    destructive: bool              # déclenche un instantané avant exécution
    schema_version: int

class Risk(IntEnum):
    READ = 0
    LOCAL_WRITE = 1
    IMPACT = 2
    IRREVERSIBLE = 3
```

**Appel d'outil**

```python
@dataclass
class ToolCall:
    id: Ref[ToolCall]
    step: Ref[Step]
    tool: str
    args: dict
    args_hash: str                 # SHA-256 de la forme canonique (voir 6.3)
    idempotency_key: str
    state: Literal["proposed", "authorized", "denied", "running", "done", "failed"]
    decision: GuardianDecision | None
    snapshot: Ref[Artifact] | None # instantané pris avant exécution si `destructive`
    result: dict | None
```

**Règles**
- Tout appel passe par `proposed → authorized` (K1). Sinon `denied`.
- Les `args` sont validés contre `input_schema` **avant** la soumission au Gardien.
- `needs_secrets` est résolu par le courtier d'identifiants au moment de l'exécution ; la valeur n'entre jamais dans `args`, le résultat ni les logs (K4).
- Un outil `destructive` déclenche la création d'un instantané avant exécution ; l'échec de l'instantané **bloque** l'exécution.
- Le résultat d'un outil qui lit du contenu externe est marqué `untrusted` ; il est fourni au modèle comme **donnée citée**, jamais comme instruction.

### 4.4 Memory

Stockage durable, organisé en arborescence de fichiers, indexé par SQLite.

```python
@dataclass
class MemoryItem:
    id: Ref[MemoryItem]
    layer: Literal["semantic", "episodic", "procedural", "project"]
    path: str                      # ex. "projects/mon-projet.md"
    content: str
    provenance: Provenance
    sensitivity: Literal["normal", "sensitive"]
    links: list[Ref[MemoryItem]]
    status: Literal["active", "superseded", "forgotten"]
    supersedes: Ref[MemoryItem] | None
    created_at: str
    updated_at: str
    schema_version: int

@dataclass
class Provenance:
    kind: Literal["stated", "observed", "inferred"]
    source: str                    # tâche, conversation, outil, fichier
    confidence: float | None       # uniquement pour "inferred"
    confirmed_by_user: bool        # une entrée inferred confirmée devient stated
```

**Opérations** : `write`, `read(scope)`, `search(query, scope)`, `supersede`, `forget`, `propose_consolidation`.

**Règles**
- Les **fichiers Markdown sont la source de vérité** ; l'index SQLite et les embeddings sont reconstructibles.
- L'accès est filtré par `memory_scope` de l'agent (K9) ; une lecture hors scope est refusée et journalisée.
- `inferred` n'est jamais promu en `stated` sans confirmation de l'utilisateur (K8).
- Les entrées `sensitive` ne sont créées que si l'utilisateur a activé la catégorie correspondante.
- `forget` supprime le contenu **et** ses dérivés (index, embeddings, extraits de contexte persistés). Un `forget` émet un événement sans le contenu supprimé.
- `propose_consolidation` produit une **liste de propositions** (fusion, résumé, suppression) ; le kernel n'applique rien sans validation.

```python
@dataclass
class MemoryScope:
    read: list[str]                # globs de chemins, ex. ["projects/**", "machines/**"]
    write: list[str]
    allow_sensitive: bool
```

### 4.5 Model

Description d'un modèle et contrat d'appel.

```python
@dataclass
class Model:
    id: Ref[Model]
    name: str                      # "qwen36-35b-moe"
    backend: Literal["local", "cloud"]
    node: Ref[Node] | None         # nœud qui l'héberge (local)
    endpoint: str                  # URL d'une API compatible OpenAI
    context_window: int
    capabilities: set[str]         # {"tools", "vision", "json"}
    runtime_params: dict           # offload, contexte, quantification, threads
    measured: Measured | None      # rempli par le Model Lab
    data_policy: DataPolicy
    cost_per_mtok: tuple[float, float] | None   # (entrée, sortie), None si local
    schema_version: int

@dataclass
class Measured:
    gen_tps: float
    prompt_tps: float
    ttft_ms: int
    ram_mb: int
    vram_mb: int
    tool_call_reliability: float   # 0-1, sur les tâches de référence
    measured_at: str

@dataclass
class DataPolicy:
    allowed_classes: set[str]      # {"public", "project_code"} ; vide pour du local sans limite
```

**Contrat d'appel**

```python
class ModelClient(Protocol):
    def generate(self, messages: list[Message], *, tools: list[ToolSpec] | None,
                 params: GenParams) -> Iterator[Chunk]: ...
```

**Règles**
- Le kernel ne choisit pas le modèle : il reçoit un `ModelChoice` du router (avec la raison) et le consigne dans l'étape.
- Pour un modèle `cloud`, la classe de données du contexte est évaluée **avant** l'appel ; une classe non autorisée interdit l'appel (K10).
- Chaque appel consigne : modèle, nombre de tokens, latence, coût, raison du choix.

### 4.6 Node

Machine ou appareil du réseau.

```python
@dataclass
class Node:
    id: Ref[Node]
    name: str
    roles: set[Literal["main", "lab", "client", "sensor"]]
    address: str                   # adresse réseau privée
    resources: Resources           # cpu, ram_mb, gpus[(name, vram_mb)]
    models: list[Ref[Model]]
    tools: list[str]               # outils exécutables sur ce nœud
    status: Literal["online", "degraded", "offline"]
    last_heartbeat: str
    schema_version: int
```

**Opérations** : `register`, `heartbeat`, `lease_gpu`, `release_gpu`, `exec(tool_call)`.

**Règles**
- Un nœud est `offline` s'il manque N battements consécutifs ; ses tâches en cours passent en reprise sur un autre nœud si les outils le permettent.
- **Bail GPU** : un seul bail actif par GPU, avec TTL ; l'inférence demande un bail avant l'appel. Cela matérialise la contrainte « une inférence à la fois par GPU ».
- Le nœud principal est un point de défaillance connu : l'état est sauvegardable et un autre nœud peut le reprendre (phase 2).

### 4.7 Skill

Routine réutilisable, apprise ou écrite.

```python
@dataclass
class Skill:
    id: Ref[Skill]
    name: str
    version: int
    description: str
    params_schema: dict
    steps: list[SkillStep]         # appels d'outils paramétrés + contrôles
    required_tools: list[str]
    risk: Risk                     # maximum des risques des étapes (calculé)
    origin: Literal["written", "learned", "forged"]
    status: Literal["draft", "active", "disabled"]
    content_hash: str
    golden: list[Ref[Task]]        # tâches de référence associées
    schema_version: int

@dataclass
class SkillStep:
    tool: str
    args_template: dict            # avec variables {param}
    checks: list[Check]
    on_failure: Literal["abort", "retry", "ask"]
```

**Règles**
- Une skill **n'étend jamais** les droits : exécutée par un agent, chaque appel passe par le Gardien comme n'importe quel autre.
- `risk` est calculé, jamais déclaré.
- Une skill `learned` ou `forged` démarre en `draft` ; l'activation exige une validation humaine qui enregistre `content_hash`.
- Toute modification incrémente `version`, retombe en `draft` et invalide l'activation précédente.
- Une skill s'exécute comme une `Task` ordinaire (checkpoints, reprise, événements).

### 4.8 Event

Journal append-only de tout ce qui se passe.

```python
@dataclass
class Event:
    id: Ref[Event]
    ts: str
    type: str                      # "task.started", "approval.granted"…
    source: str                    # nœud/agent/interface émetteur
    subject: str | None            # id de l'objet concerné
    correlation_id: str            # regroupe une demande de bout en bout
    causation_id: str | None       # événement qui a causé celui-ci
    payload: dict                  # sans secret, sans contenu mémoire supprimé
    schema_version: int
```

**Règles**
- Écriture append-only (K5) ; aucune mise à jour, aucune suppression (sauf politique de rétention explicite, qui supprime par plages et laisse un événement `log.pruned`).
- Livraison **au moins une fois** aux consommateurs, avec un offset par consommateur ; les consommateurs DOIVENT être idempotents.
- Les événements sont la base de l'audit, du rejeu, de l'observabilité et des déclencheurs.
- Le **score d'importance** (ce qui mérite d'interrompre l'utilisateur) n'est pas calculé par le kernel : c'est le rôle du moteur d'initiative.

**Catalogue minimal**

| Famille | Types |
|---|---|
| Task | `task.created`, `started`, `waiting_approval`, `waiting_input`, `paused`, `resumed`, `verifying`, `repairing`, `succeeded`, `failed`, `cancelled` |
| Step / Tool | `step.started`, `step.done`, `tool.proposed`, `tool.authorized`, `tool.denied`, `tool.done`, `tool.failed` |
| Approval | `approval.requested`, `granted`, `denied`, `expired`, `revoked` |
| Memory | `memory.written`, `superseded`, `forgotten`, `consolidation.proposed` |
| Model | `model.called`, `model.escalated`, `model.budget_exceeded` |
| Node | `node.registered`, `online`, `degraded`, `offline`, `gpu.leased`, `gpu.released` |
| Système | `agent.suspended`, `killswitch.engaged`, `killswitch.released`, `log.pruned`, `snapshot.created`, `snapshot.restored` |
| Externes | `external.message_received`, `external.file_changed`, `external.timer` |

### 4.9 Approval

Décision humaine liée à une action précise.

```python
@dataclass
class Approval:
    id: Ref[Approval]
    tool_call: Ref[ToolCall]
    task: Ref[Task]
    action_summary: str            # texte exact présenté à l'utilisateur
    action_hash: str               # = args_hash + tool + version (K3)
    risk: Risk
    state: Literal["pending", "granted", "denied", "expired", "revoked"]
    scope: Literal["this_call", "this_task"]
    issuer: Literal["guardian"]    # seul émetteur possible (K2)
    decided_by: Literal["user", "policy"] | None
    channel: str | None            # cli, web, voice, phone…
    requested_at: str
    expires_at: str
    decided_at: str | None
```

**États**

```
pending ─► granted ─► (consommée par l'exécution)
   │
   ├─► denied
   ├─► expired   (aucune réponse avant expires_at)
   └─► revoked   (retirée avant exécution, ex. coupe-circuit)
```

**Règles**
- Seul le Gardien crée et décide une approbation (K2) ; l'agent ne reçoit que le résultat.
- Valide **uniquement** pour l'`action_hash` approuvé (K3) ; si les arguments changent, une nouvelle approbation est requise.
- `scope = this_task` n'est autorisé que pour les risques `impact` ; le risque `irreversible` impose `this_call`.
- Une approbation expire ; une approbation expirée équivaut à un refus.
- Le texte présenté (`action_summary`) est généré par le kernel à partir des arguments réels, **jamais** rédigé par l'agent, pour éviter qu'un agent induise l'utilisateur en erreur.

### 4.10 Artifact

Sortie durable d'une tâche.

```python
@dataclass
class Artifact:
    id: Ref[Artifact]
    task: Ref[Task] | None
    kind: Literal["report", "patch", "file", "benchmark", "log", "snapshot", "transcript"]
    path: str                      # sous data/artifacts/, adressage par hachage
    sha256: str
    media_type: str
    size_bytes: int
    provenance: ArtifactProvenance
    sensitivity: Literal["normal", "sensitive"]
    retention_days: int | None
    created_at: str
    schema_version: int

@dataclass
class ArtifactProvenance:
    models: list[Ref[Model]]
    tool_calls: list[Ref[ToolCall]]
    memory_inputs: list[Ref[MemoryItem]]
```

**Règles**
- Immuable (K11) : contenu adressé par hachage ; une « modification » crée un nouvel artefact qui référence l'ancien.
- Les **instantanés** de workspace sont des artefacts de type `snapshot` ; ils servent à `dindon undo`.
- La provenance permet de répondre à « d'où vient ce résultat ? » : quel modèle, quels appels d'outils, quelle mémoire.
- La rétention est explicite ; la purge émet un événement.

---

## 5. Flux de référence

### 5.1 Appel d'outil de bout en bout

```
Agent ─► propose ToolCall ──────────────────────────────┐
                                                         ▼
                              1. valider args contre input_schema
                              2. calculer args_hash, idempotency_key
                              3. écrire Step(intent) + événement tool.proposed
                                                         │
                                                         ▼
                                              GARDIEN décide
                              ┌──────────────┬───────────┴───────────┐
                              ▼              ▼                       ▼
                            allow           ask                     deny
                              │              │                       │
                              │       créer Approval(pending)        ▼
                              │       Task → waiting_approval   ToolCall.denied
                              │       attendre l'utilisateur    retour à l'agent
                              │              │
                              │        granted ? ──non──► denied / expired
                              │              │oui
                              ▼              ▼
                    4. si destructive : créer l'instantané (échec ⇒ blocage)
                    5. résoudre les secrets par le courtier (jamais dans args)
                    6. exécuter dans le bac à sable du nœud, avec timeout
                    7. écrire Step(done) + résultat (marqué untrusted si contenu externe)
                    8. émettre tool.done ; checkpoint
```

### 5.2 Vie d'une tâche

```
task.created ─► task.started
   │
   ├─ boucle d'étapes (modèle ⇄ outils), checkpoint à chaque étape
   │
   ├─ l'agent déclare avoir fini ─► task.verifying
   │        ├─ tous les Check passent ──► task.succeeded + Artifact(report)
   │        └─ un Check échoue ─┬─ budget de réparation restant ─► task.repairing ─► retour à la boucle
   │                            └─ sinon ─► task.failed
   │
   └─ crash ─► redémarrage ─► relecture des étapes ─► reprise (voir 4.2)
```

### 5.3 Coupe-circuit

`dindon stop` (Gardien) : toutes les tâches `running` passent en `paused`, les approbations `pending` en `revoked`, les agents en `suspended`, événement `killswitch.engaged`. La levée est une action humaine explicite.

---

## 6. Persistance

### 6.1 Principes

- **SQLite en mode WAL** pour l'état transactionnel : tâches, étapes, appels d'outils, approbations, événements, nœuds, modèles, index mémoire.
- **Fichiers** pour ce qui est lisible par un humain : mémoire (Markdown), identité, définitions d'agents, skills.
- **Artefacts** adressés par hachage sous `data/artifacts/`.
- Une **seule transaction** écrit l'état et l'événement correspondant (K6).
- Migrations numérotées, appliquées au démarrage ; `schema_version` dans chaque ligne sérialisée.

### 6.2 Schéma (extrait)

```sql
CREATE TABLE events (
  seq            INTEGER PRIMARY KEY AUTOINCREMENT,   -- ordre total local
  id             TEXT NOT NULL UNIQUE,
  ts             TEXT NOT NULL,
  type           TEXT NOT NULL,
  source         TEXT NOT NULL,
  subject        TEXT,
  correlation_id TEXT NOT NULL,
  causation_id   TEXT,
  payload        TEXT NOT NULL,                       -- JSON, sans secret
  schema_version INTEGER NOT NULL
);
CREATE INDEX events_subject ON events(subject, seq);
CREATE INDEX events_type    ON events(type, seq);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, parent_id TEXT,
  goal TEXT NOT NULL, input TEXT NOT NULL,
  state TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
  budget TEXT NOT NULL, verification TEXT NOT NULL, schedule TEXT,
  result_artifact TEXT, error TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  schema_version INTEGER NOT NULL
);

CREATE TABLE steps (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
  idx INTEGER NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
  payload TEXT NOT NULL, result TEXT,
  started_at TEXT NOT NULL, finished_at TEXT,
  tool_idempotent INTEGER, idempotency_key TEXT,
  UNIQUE (task_id, idx)
);

CREATE TABLE tool_calls (
  id TEXT PRIMARY KEY, step_id TEXT NOT NULL REFERENCES steps(id),
  tool TEXT NOT NULL, args TEXT NOT NULL, args_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL, decision TEXT, snapshot_artifact TEXT, result TEXT
);

CREATE TABLE approvals (
  id TEXT PRIMARY KEY, tool_call_id TEXT NOT NULL REFERENCES tool_calls(id),
  task_id TEXT NOT NULL REFERENCES tasks(id),
  action_summary TEXT NOT NULL, action_hash TEXT NOT NULL,
  risk INTEGER NOT NULL, state TEXT NOT NULL, scope TEXT NOT NULL,
  decided_by TEXT, channel TEXT,
  requested_at TEXT NOT NULL, expires_at TEXT NOT NULL, decided_at TEXT
);

CREATE TABLE artifacts (
  id TEXT PRIMARY KEY, task_id TEXT, kind TEXT NOT NULL,
  path TEXT NOT NULL, sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
  size_bytes INTEGER NOT NULL, provenance TEXT NOT NULL,
  sensitivity TEXT NOT NULL DEFAULT 'normal',
  retention_days INTEGER, created_at TEXT NOT NULL
);
```

Les tables `nodes`, `models`, `skills` et l'index `memory_items` suivent le même modèle (clé `id`, JSON pour les sous-structures, `schema_version`).

### 6.3 Forme canonique des arguments

Pour que `args_hash` soit stable :

1. JSON avec clés triées, sans espaces superflus, encodage UTF-8 ;
2. nombres normalisés (pas de `1.0` contre `1`) ;
3. chemins de fichiers résolus en chemins absolus **dans le bac à sable** ;
4. le hachage inclut `tool.name` et `tool.version`.

---

## 7. Modèle d'erreurs

| Classe | Exemple | Traitement par défaut |
|---|---|---|
| `Retryable` | Délai dépassé, serveur d'inférence indisponible | Réessai avec attente croissante, puis repli du router, puis `failed` |
| `ToolError` | Commande en échec, fichier absent | Renvoyé à l'agent comme observation ; compté dans le budget d'étapes |
| `PolicyDenied` | Refus du Gardien | Renvoyé à l'agent ; **jamais** réessayé tel quel |
| `ApprovalExpired` | Aucune réponse à temps | Assimilé à un refus ; la tâche peut proposer une alternative |
| `BudgetExceeded` | Tokens, étapes, durée ou coût cloud dépassés | Tâche `failed` avec rapport partiel ; événement dédié |
| `VerificationFailed` | Un `Check` échoue | Réparation tant qu'il reste du budget, sinon `failed` |
| `Interrupted` | Arrêt, crash | Reprise (voir 4.2) |
| `ModelUnavailable` | Aucun modèle éligible | Repli, escalade autorisée, sinon `waiting_input` |
| `IntegrityError` | Violation d'invariant (K1 à K12) | Arrêt de la tâche, agent `suspended`, événement de sécurité |

---

## 8. Tests de conformité

Ces tests définissent ce que « kernel terminé » veut dire. Ils tournent sans réseau, avec le **faux serveur LLM**, une horloge injectée et une base SQLite temporaire.

| # | Test | Invariant |
|---|---|---|
| T1 | Un appel d'outil sans décision du Gardien est refusé à l'exécution | K1 |
| T2 | Un agent qui tente de créer ou valider une `Approval` échoue | K2 |
| T3 | Une approbation ne s'applique pas si un seul argument change | K3 |
| T4 | Un secret passé à un outil n'apparaît dans aucun événement, artefact ni log | K4 |
| T5 | `UPDATE` ou `DELETE` sur `events` est impossible via l'API du kernel | K5 |
| T6 | Un échec d'écriture de l'événement annule la transition d'état | K6 |
| T7 | **Crash pendant une étape de modèle** : reprise et poursuite | K7 |
| T8 | **Crash pendant un outil idempotent** : reprise avec la même clé, effet unique | K7 |
| T9 | **Crash pendant un outil non idempotent** : pas de relance, tâche en `waiting_input` | K7 |
| T10 | Une entrée `inferred` ne peut pas être lue comme `stated` ; la promotion exige confirmation | K8 |
| T11 | Une lecture mémoire hors `memory_scope` est refusée et journalisée | K9 |
| T12 | Un appel cloud avec une classe de données non autorisée est bloqué avant l'envoi | K10 |
| T13 | Modifier un artefact crée un nouvel artefact et laisse l'ancien intact | K11 |
| T14 | Le kernel s'exécute avec le réseau désactivé | K12 |
| T15 | Rejouer un journal d'événements avec le faux LLM reproduit le même état final | §1 |
| T16 | Un outil `destructive` dont l'instantané échoue n'est pas exécuté | §4.3 |
| T17 | Un `Check` déterministe qui échoue empêche `succeeded` | §4.2 |
| T18 | Le coupe-circuit suspend les agents et révoque les approbations en attente | §5.3 |

**Critère de sortie de la phase 0** : T1, T3, T4, T5, T6, T7, T8, T9, T14 et T15 passent. Les autres sont requis à la fin de la phase 1.

---

## 9. Hors du kernel

Ces éléments utilisent le kernel mais n'en font pas partie :

| Élément | Où il vit |
|---|---|
| Choix du modèle (politiques du router, escalade cloud) | `dindon/llm/` |
| Model Lab, tâches de référence | `dindon/llm/` |
| Règles et implémentation du Gardien | `dindon/safety/` (le kernel ne connaît que son **protocole**) |
| Coffre à secrets et courtier d'identifiants | `dindon/safety/` |
| Boucle d'agent et construction des prompts | `dindon/runtime/` |
| Connecteurs MCP | `dindon/connectors/` |
| Canaux, voix, interface web | `dindon/channels/`, `interfaces/` |
| Onboarding, moteur d'initiative, identité | `dindon/onboarding/`, `companion/` |
| Consolidation de la mémoire (l'algorithme) | `dindon/memory/` (le kernel n'expose que `propose_consolidation`) |

**Protocole du Gardien**, connu du kernel :

```python
class Guardian(Protocol):
    def decide(self, call: ToolCall, ctx: DecisionContext) -> GuardianDecision: ...
    def request_approval(self, call: ToolCall, summary: str) -> Approval: ...
    def kill(self, reason: str) -> None: ...
    def release(self) -> None: ...

@dataclass
class GuardianDecision:
    verdict: Literal["allow", "ask", "deny"]
    reason: str
    rule_id: str | None
```

---

## 10. Périmètre par phase

| Primitive | Phase 0 | Phase 1 | Plus tard |
|---|---|---|---|
| **Agent** | Un agent défini en YAML | Plusieurs agents, scopes mémoire, limites | Génération par intention |
| **Task** | États, étapes, checkpoints, reprise | Délégation parent/enfant, `verifying`, réparation | Planification de nuit (phase 4) |
| **Tool** | Outils de base, schémas, risques | Instantanés, sondes d'état | Connecteurs MCP (phase 3) |
| **Memory** | Fichiers simples, provenance | Quatre couches, scopes, `forget` complet | Consolidation (phase 4) |
| **Model** | Un modèle local | Mesures du Model Lab | Cloud et politique de données (phase 2) |
| **Node** | Un seul nœud local | — | Multi-nœuds, baux GPU (phase 2) |
| **Skill** | Schéma seulement | — | Exécution et apprentissage (phase 4) |
| **Event** | Journal complet | Consommateurs avec offsets | Export, observabilité |
| **Approval** | Gardien en processus, validation CLI | Gardien séparé (conteneur) | Approbation vocale et téléphone |
| **Artifact** | Rapports, logs | Instantanés, provenance | Rétention avancée |

**Phase 0 :** le Gardien est une **implémentation en processus** du même protocole que le futur service séparé. Le kernel n'a ainsi aucun changement à subir quand le Gardien devient un conteneur à part (phase 1).

---

## 11. Questions ouvertes

À trancher pendant la phase 0, avant de figer la version 1.0 de ce document :

1. **Contenu des checkpoints de modèle** : stocker les messages complets (reprise exacte, base plus volumineuse) ou un résumé compacté (plus léger, reprise approximative) ?
2. **Granularité** : un checkpoint par étape suffit-il pour les appels de modèle très longs en streaming, ou faut-il des points intermédiaires ?
3. **Parallélisme d'une tâche** : le kernel autorise-t-il des étapes d'outils concurrentes au sein d'une même tâche, ou uniquement séquentielles au début ?
4. **Langage des skills** : étapes strictement déclaratives (plus sûr, moins expressif) ou fragments de code exécutés en bac à sable ?
5. **Embeddings** : modèle, dimension et stockage (SQLite avec extension vectorielle ou fichier séparé) ; critère de reconstruction après `forget`.
6. **Horloge entre nœuds** : tolérance à la dérive pour l'expiration des approbations et l'ordre des événements ; faut-il un horodatage logique en plus ?
7. **Sondes d'état** : format générique pour qu'un outil non idempotent puisse prouver s'il a ou non produit son effet.
8. **Rétention du journal** : durée par défaut et stratégie de compactage sans casser le rejeu.
9. **Granularité des classes de données** (K10) : liste initiale (`public`, `project_code`, `personal`, `secret`) suffisante ?

Chaque question tranchée fait l'objet d'une courte entrée datée dans un journal de décisions (`docs/decisions.md`) et d'une mise à jour de ce document.
