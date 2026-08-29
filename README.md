# todo_workflows

Home Assistant Integration fuer Todo-Workflows

## Installation ueber HACS

1. HACS oeffnen -> Menue (drei Punkte) -> Benutzerdefinierte Repositories
2. Diese Repository-URL hinzufuegen
3. Kategorie entsprechend waehlen und installieren

## Lovelace-Karte

Dieses Repository enthaelt zusaetzlich die passende Custom Card unter `www/todo-workflows-card.js`.
Nach der Installation:

1. `www/todo-workflows-card.js` nach `<config>/www/todo-workflows-card.js` kopieren
2. Als Lovelace-Ressource hinzufuegen: Einstellungen -> Dashboards -> Ressourcen -> `/local/todo-workflows-card.js` (Typ: JavaScript-Modul)
