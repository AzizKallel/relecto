# Contenu clinique Relecto

Ce dossier appartient au responsable clinique. Il ne contient **aucune donnée patient**.

| Dossier | Contenu |
| --- | --- |
| `lexicon/` | Vocabulaire : résultats, côtés, négations, anatomie latéralisée |
| `rule-packs/` | Une fiche par examen : sections obligatoires, technique, règles activées et leur gravité |
| `golden-sets/` | Comptes rendus de référence annotés, utilisés pour tester chaque version |
| `examples/` | Exemples de comptes rendus pour les démonstrations |

**Processus de modification**

1. Modifier le fichier et augmenter son numéro de version.
2. Lancer `./relecto gate` (ou laisser la vérification automatique sur GitHub le faire).
3. Un second radiologue relit la modification avant sa mise en production.

Format du jeu de référence (`golden-sets/*.jsonl`) : une ligne par compte rendu,
`{"id": "...", "exam": "ct_chest", "text": "...", "expected": ["LAT-001", ...]}`.
Uniquement des comptes rendus synthétiques ou dé-identifiés.
