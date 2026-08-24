# KI-Protokoll — vom Besichtigungsmaterial zur Kundentabelle

Erster Aufschlag für das Deliverable hinter dem **KI-Protokoll-Button**: aus
Textnotizen, Fotos und Sprachnotizen einer Besichtigung entsteht die **Tabelle
im Zielformat des Kunden** — und dort, wo das Material nicht reicht, eine
**To-do-Liste** statt einer erfundenen Zelle.

Läuft komplett offline: kein Azure, keine Credentials, keine Fremdpakete.

```bash
python ki_protokoll_demo.py                 # Lauf 1 (Lücken) -> Ergänzung -> Lauf 2 (vollständig)
python ki_protokoll_demo.py --fliesstext    # zusätzlich das optionale Prosa-Protokoll
python ki_protokoll_demo.py --csv           # zusätzlich der CSV-Export
python -m pytest tests/test_ki_protokoll.py -q
```

---

## Der Ablauf

Ein Knopf, beliebig oft drückbar. Zwischen zwei Drücken ändert sich nur das
Material im Besichtigungs-Export.

```
Notizen / Fotos / Sprachnotizen
        |
        v
   Extraktion            jede Zelle bekommt Wert + Konfidenz + Quell-Notiz-IDs
        |
        v
   Vollständigkeits-     Pflichtzelle leer? unter Schwelle? widersprüchlich?
   prüfung
        |
        +--> vollständig  ->  Tabelle im Kundenformat (+ optional Fließtext)
        |
        +--> offen        ->  Tabelle MIT sichtbaren Lücken
                              + Benachrichtigung mit To-do-Liste
                                (pro Position, pro Spalte, mit Erfassungsart)
        |
   Nutzer ergänzt Foto / Sprachnotiz / Textnotiz im Besichtigungs-Export
        |
        v
   Button erneut  ->  was wurde geschlossen, was fehlt noch, neue Tabelle
```

### Was der Nutzer nach Lauf 1 sieht

```
Protokoll erzeugt - 11 Angabe(n) offen

11 Pflichtangabe(n) konnten nicht zuverlaessig abgeleitet werden. Bitte im
Besichtigungs-Export ergaenzen und danach erneut auf "KI-Protokoll" tippen.

Kopfdaten:
  [ ] Auftragsnummer (fehlt) -> Textnotiz: Auftragsnummer des Kunden, Format JJJJ-NNN.
Bad / Fliesenspiegel:
  [ ] Menge (fehlt) -> Sprachnotiz: Aufmass zur Position nennen, z. B. "ca. 4,5 Quadratmeter".
  [ ] Foto-Nr. (fehlt) -> Foto: Mindestens ein Foto der Position aufnehmen.
Treppenhaus / Handlauf:
  [ ] Zustand (unsicher) -> Sprachnotiz: Eine klare Zustandsaussage zur Position.
  ...
```

Und dazu die Tabelle — mit `-- offen --` für fehlende und `?` für unsicher
abgeleitete Pflichtangaben. Das Protokoll wird also **immer** erzeugt; die
Lücken sind sichtbar, nicht verschwiegen.

### Nach Lauf 2

```
Protokoll vollstaendig
Deine Ergaenzungen haben die letzten 11 offenen Punkte geschlossen.
```

Bei nur teilweiser Ergänzung: *„3 Punkt(e) durch deine Ergaenzungen
geschlossen"* plus die verbliebene Liste — die Rückmeldung, welche Punkte noch
aufgenommen werden müssen.

---

## Drei Entscheidungen, die den Rest tragen

**1. Konfidenz pro Zelle, nicht pro Protokoll.**
Jede Zelle hält Wert, Konfidenz (0–1) und die IDs der Notizen, aus denen sie
stammt. Ohne diese Granularität lässt sich nicht sagen, *welche* Einträge nicht
zuverlässig abgeleitet werden konnten — und genau das ist die Frage, die der
Nutzer nach dem Knopfdruck beantwortet haben will.

Die Quelle bestimmt die Basis-Konfidenz: Textnotiz 0.92, Sprachnotiz 0.85,
Bildbeschreibung 0.68. Letztere liegt bewusst **unter** der Schwelle von 0.7 —
etwas, das nur aus einem Foto abgeleitet wurde, wird bestätigt, nicht
unterschrieben. Relativierungen im Material („wirkt lose", „vermutlich") ziehen
0.25 ab, zwei unabhängig übereinstimmende Notizen geben 0.05 dazu.

**2. Nie erfinden.** Eine nicht ableitbare Zelle bleibt leer und begründet, warum
— eine erfundene Zelle im Kundenprotokoll ist schlimmer als eine sichtbare
Lücke. Dieselbe Regel prüft `llm.py` im Code nach, nicht nur im Prompt.

**3. Drei Status, nicht zwei.** `fehlt` (nichts im Material), `unsicher` (etwas,
aber nicht belastbar) und `widerspruch` (zwei Notizen sagen Verschiedenes)
brauchen drei verschiedene Sätze an den Nutzer — und der Widerspruch braucht
eine Entscheidung, kein weiteres Foto.

---

## Das Kundenformat steckt in einer JSON-Datei

`schemas/kunde_besichtigung_v1.json` beschreibt Spalten, Pflichtangaben,
erlaubte Werte, Ableitungsstrategie und Erfassungshinweis. **Ein anderes
Kundenformat heißt: diese Datei ersetzen — kein Code.**

```json
{
  "id": "prioritaet",
  "label": "Prioritaet",
  "pflicht": true,
  "typ": "auswahl",
  "erlaubteWerte": ["sofort", "kurzfristig", "mittelfristig", "keine"],
  "erfassungshinweis": "Dringlichkeit nennen, z. B. \"muss sofort gemacht werden\".",
  "empfohleneErfassung": "Sprachnotiz",
  "extraktion": {"strategie": "auswahl_synonyme", "synonyme": {"sofort": ["sofort", "dringend", "gefahr"]}}
}
```

Der `erfassungshinweis` ist das, was die To-do-Liste konkret macht:
nicht „Priorität fehlt", sondern „Sprachnotiz: Dringlichkeit nennen".

> Das mitgelieferte Schema ist ein **Platzhalter** — eine plausible
> Besichtigungstabelle (Pos., Raum, Bauteil, Zustand, Feststellung, Menge,
> Einheit, Maßnahme, Priorität, Foto-Nr.). Sobald das echte Kundenformat
> vorliegt, wird diese Datei danach umgebaut.

---

## Regel-Extraktor jetzt, Modell danach

`extraction.py` arbeitet deterministisch mit Stichwortlisten und Mustern aus dem
Schema. Das macht Tabelle, Lückenlogik, Benachrichtigung und zweiten Durchgang
**heute** vorführbar und testbar — aber echte Baustellennotizen („die Fuge unten
rechts ist hinüber") schlägt keine Stichwortliste.

`llm.py` ist derselbe Schritt über das geteilte Foundry-Deployment: gleiches
Schema, gleiches Zellenformat, gleiche Konfidenz-Semantik. Beide erfüllen
dieselbe `Extractor`-Signatur, `service.py` kennt den Unterschied nicht:

```python
from ki_protokoll.llm import extract_table_with_llm
ki_protokoll_erzeugen("BES-2026-0087", extractor=extract_table_with_llm)
```

**Status:** `llm.py` ist gegen denselben Client geschrieben wie die vier Agenten,
aber **noch nicht gegen ein echtes Deployment gelaufen** — in dieser Umgebung
gibt es keinen Foundry-Endpunkt. Der Regel-Extraktor bleibt Default, bis das
nachgeholt ist.

---

## Fließtext: optional, aus der Tabelle

`mit_fliesstext=True` erzeugt zusätzlich das bisherige Prosa-Protokoll —
**aus der fertigen Tabelle**, nicht aus den Notizen. So können Tabelle und Text
nie zwei verschiedene Geschichten erzählen. Die Tabelle ist das Zielformat, die
Prosa ist nice to have.

---

## Dateien

| Datei | Aufgabe |
|---|---|
| `service.py` | Der Button: `ki_protokoll_erzeugen(besichtigung_id, mit_fliesstext=False)` |
| `schema.py` + `schemas/*.json` | Das Kundenformat |
| `extraction.py` | Notizen/Fotos/Sprache → Zellen mit Konfidenz und Belegen |
| `llm.py` | Derselbe Schritt über Foundry (Andockstelle, ungetestet) |
| `completeness.py` | Welche Pflichtzellen offen sind, und der Lauf-zu-Lauf-Vergleich |
| `notification.py` | Benachrichtigung und To-do-Liste |
| `render.py` | Tabelle (Markdown/CSV) und optionaler Fließtext |
| `store.py` | Besichtigungs-Export und Lauf-Historie (in-memory, siehe Hinweis im Modul) |
| `mock_data/` | Synthetischer Export plus Nachtrag für den zweiten Durchgang |

---

## Offen für die nächste Runde

* **Echtes Kundenformat** einsetzen (JSON ersetzen).
* **`llm.py` scharf schalten** und gegen echte Notizen/Transkripte messen —
  inklusive Vision-Modell für die Bildbeschreibungen, die hier als Text
  vorliegen.
* **Persistenz**: `store.py` liegt im Prozessspeicher; für die App an deren
  Storage anbinden.
* **Schwellenwert kalibrieren**: 0.7 ist gesetzt, nicht gemessen. Sobald echte
  Protokolle vorliegen, gegen die Korrekturquote der Nutzer justieren.
* **Excel-Export** im Kundenlayout (heute CSV mit Semikolon).
