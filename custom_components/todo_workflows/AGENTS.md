# AGENTS: todo_workflows

Diese Anleitung richtet sich an zukunftige Agents, die an der Integration todo_workflows und der zugehorigen Custom Card arbeiten.

## 1) Ziel und Scope

Die Integration verwaltet eine eigene persistente Home-Assistant-Todo-Liste und erweitert sie um:
- Eine eigene persistente Standardliste (`todo.todo_workflows`), die beim Einrichten des Config-Entries angelegt wird
- Upsert von Aufgaben anhand einer stabilen Identifikation (ident)
- Abschluss-Logik mit persistenten Aufgaben (statt sofortigem Entfernen)
- Optionales Auto-Cleanup fur erledigte persistente Aufgaben
- Einheitliche Item-Ausgabe per WebSocket fur Frontends

Die Custom Card zeigt diese Daten an, bietet ein Add-Formular und Completion direkt im UI.

## 2) Wichtige Dateien

Backend (Integration):
- custom_components/todo_workflows/__init__.py
- custom_components/todo_workflows/todo.py
- custom_components/todo_workflows/condition.py
- custom_components/todo_workflows/const.py
- custom_components/todo_workflows/services.yaml
- custom_components/todo_workflows/config_flow.py
- custom_components/todo_workflows/manifest.json

Frontend (Custom Card):
- www/todo-workflows-card.js

## 3) Datenmodell (Beschreibung als JSON)

Die Integration speichert Metadaten im Feld description als JSON-String.
Relevante Felder:
- title
- ident
- description
- badge
- due
- priority
- icon
- color
- second_color
- icon_background_color
- icon_color
- text_color
- persistent
- resolved_text
- cleanup_hours
- completed_at

Wichtig:
- ident ist der primare Schlussel fur Wiederfinden/Upsert.
- Wenn ident fehlt, wird title als Fallback genutzt.
- completed_at + cleanup_hours steuern Auto-Cleanup fur persistente completed Items.

## 4) Services, Conditions und WebSocket

Registrierte Services (Domain todo_workflows):
- upsert_item
- complete_item_v2

Registrierte Condition (Automation/Skript):
- todo_workflows.has_ident

WebSocket Command:
- type: todo_workflows/list_items

Lovelace-Resource:
- Die Card wird in Lovelace Storage Mode automatisch als `module`-Resource unter `/todo_workflows_frontend/todo-workflows-card.js?v={version}` angelegt und bei Versionswechsel aktualisiert.
- Im YAML-Resource-Mode muss die Resource in `configuration.yaml` gepflegt werden; die Integration schreibt YAML nicht um.

Verhalten:
- Die eigene Liste `todo.todo_workflows` ist die persistente Backend-Entity. Die Card kommuniziert ausschliesslich mit `todo_workflows`-Services und dem WebSocket und kennt keine Todo- oder Sensor-Entity.
- upsert_item:
  - sucht Item per ident/titelnahen Fallbacks
  - aktualisiert vorhandenes Item oder legt neues Item an
- complete_item_v2:
  - bei persistent=true: markiert Item als completed und schreibt completed_at
  - sonst: entfernt Item aus der Liste
- todo_workflows.has_ident:
  - pruft, ob ein Item mit ident existiert
  - optional mit completed=true/false zur Status-Prufung
  - liest direkt aus registrierten Todo-Entities oder States/Fallback-Sensoren (kein aktiver todo.get_items Call im Condition-Check)
- list_items:
  - ladt Items
  - fuhrt Cleanup (falls fallig) aus
  - liefert normalisierte Struktur fur die Card

## 5) Custom Card: Architektur und Verhalten

Datei:
- www/todo-workflows-card.js

Kernpunkte:
- Nutzt Shadow DOM und rendert eine Liste mit Styling pro Item.
- Laedt Daten ausschliesslich uber callWS mit todo_workflows/list_items.
- Sendet Aenderungen ausschliesslich an todo_workflows-Services; die Todo-Liste ist ein internes Speicher-Detail.
- Unterstutzt optimistische UI beim Abschluss (direkte UI-Reaktion, danach Refresh).
- Enthalt ein Formular fur neue/aktualisierte Eintrage (Service upsert_item).

Wichtige interne Mechanismen:
- Render-Signatur zur Vermeidung unnotiger Re-Renders.
- Fetch-Cooldown und Pending-Update-Timer.
- Geplante Post-Action-Refreshes nach complete.
- Full-Reload-Intervall fur langfristige Konsistenz.

Aktuelle Refresh-Defaults der Card:
- Fetch-Cooldown: 200 ms
- Post-Action-Refresh: 150 ms und 600 ms
- Full-Reload: 5 Minuten

## 6) Arbeitsregeln fur Agents

Vor Anderungen:
- Immer zuerst den Datenfluss prufen: Card -> todo_workflows-Service/WebSocket -> Todo-Entity als Speicher -> Card-Render.
- Bei Feldanderungen description-JSON und Normalisierung synchron halten.
- Bei neuen Feldern sowohl Backend als auch Card anpassen (inkl. Form und Rendering).

Beim Backend:
- Service-Schema (voluptuous) strikt pflegen.
- Condition-Schema (target/options) und Legacy-Feldmigration (entity_id/ident/completed) konsistent halten.
- Todo-Operationen ausschliesslich im Backend ausfuhren; die Card darf keine Todo- oder Sensor-Entities ansprechen.
- Removal/Update nur mit belastbarer Identifikation ausfuhren.

Bei der Card:
- Keine unnotigen Full-Rebuilds des DOM einfuhren.
- Optimistische Updates nur nutzen, wenn danach ein Server-Refresh erfolgt.
- Farbe/Icon/Text-Kontraste auf Lesbarkeit prufen.

Pflegepflicht:
- AGENTS.md bei jeder relevanten Anderung an Backend, Services, WebSocket oder Custom Card aktualisieren.
- Mindestens die Abschnitte Datenmodell, Services/WebSocket, Stolperfallen und Test-Checkliste auf Delta pruefen.
- Keine Umsetzung als "fertig" markieren, wenn AGENTS.md nicht mitgezogen wurde.

## 7) Haufige Stolperfallen

- Unterschiedliche Item-IDs (uid, id, item_id) nicht vereinheitlicht.
- description enthalt kein valides JSON, daher immer defensiv parsen.
- todo.get_items kann je nach HA-Version unterschiedlich verschachtelte Antwortstrukturen liefern.
- Condition-Checks laufen synchron und mussen deshalb auf vorhandene States zugreifen statt Service-Calls.
- Browser-Cache kann alte Card-Versionen halten (bei JS-Anderungen mit Versionsbump arbeiten).
- Lovelace Resources koennen im YAML-Modus nicht von der Integration persistiert angelegt werden.
- Card und Integration konnen asynchron unterschiedliche Datenstande sehen; post-action refresh ist daher gewollt.
- Die Standardliste wird per Home-Assistant-Store persistiert; sie darf nicht durch fluchtigen Entity-State ersetzt werden.

## 8) Test-Checkliste fur Anderungen

Backend:
- Nach dem Einrichten des Config-Entries existiert `todo.todo_workflows`; neu angelegte Items bleiben nach einem Home-Assistant-Neustart erhalten.
- upsert_item erstellt neues Item.
- upsert_item aktualisiert vorhandenes Item per ident.
- complete_item_v2 entfernt nicht-persistentes Item.
- complete_item_v2 markiert persistentes Item als completed.
- Cleanup entfernt fallige completed/persistent Items.
- WebSocket list_items liefert erwartete normalisierte Felder.
- Condition todo_workflows.has_ident liefert true bei vorhandenem ident.
- Condition todo_workflows.has_ident respektiert optional completed=true/false.

Frontend:
- Die Card-Resource erscheint nach dem Setup in Dashboard -> Ressourcen als `module` mit einer versionsierten URL, zum Beispiel `/todo_workflows_frontend/todo-workflows-card.js?v=1.0.6`.
- Add-Form sendet alle Felder korrekt.
- Completion aktualisiert UI sofort und bleibt nach Refresh konsistent.
- Sortierung nach priority, dann due, dann title ist stabil.
- Bei WebSocket-Fehlern zeigt die Card den Fehler und verwendet keine State- oder Sensor-Fallbacks.

## 9) Definition of Done fur Agents

Eine Anderung gilt als fertig, wenn:
- Backend-Servicepfad korrekt funktioniert,
- Card-Darstellung und Interaktionen konsistent sind,
- neue/angepasste Felder in Backend und Frontend vollstandig verdrahtet sind,
- keine Regression bei persistent/cleanup Verhalten sichtbar ist.

## 10) Kurze Praxisbeispiele

Service-Beispiel (Upsert):

service: todo_workflows.upsert_item
data:
  entity_id: todo.todo_list
  title: Auto laden
  ident: auto_laden
  description: Startet Ladevorgang
  priority: 1
  icon: mdi:car-electric
  color: "#3ca55c"
  second_color: "#b5ac49"
  persistent: true
  resolved_text: Erledigt

Service-Beispiel (Complete):

service: todo_workflows.complete_item_v2
data:
  entity_id: todo.todo_list
  ident: auto_laden
  persistent: true

Lovelace-Beispiel (Card):

type: custom:todo-workflows-card
title: Aufgaben
entity: todo.todo_list
show_add_button: true
add_button_label: Add

Automation-Beispiel (Condition):

condition:
  - condition: todo_workflows.has_ident
    target:
      entity_id: todo.todo_list
    options:
      ident: auto_laden
      completed: true
