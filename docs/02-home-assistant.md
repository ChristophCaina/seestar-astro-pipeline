# Home Assistant: Add-on, Dashboard, Automationen

## Add-on jaxzin/ha-seestar (External-Modus)

1. **Einstellungen → Add-ons → Add-on Store → ⋮ → Repositories**: `https://github.com/jaxzin/ha-seestar`
2. **Seestar for Home Assistant** installieren
3. Konfiguration:

```yaml
scopes: []                          # im External-Modus leer lassen
alpaca_host: "192.168.1.XX:5555"    # seestar_alp-LXC, Alpaca-Port
alpaca_webui_port: 5432
imaging_port: 7556
# Mosquitto-Add-on: mqtt_* leer lassen. Externer Broker:
mqtt_host: "192.168.1.XX"
mqtt_port: 1883
mqtt_username: "..."
mqtt_password: "..."
```

Ein paar Dinge vorab:
- Das Gerät heißt in HA so wie `[[seestars]] name` in seestar_alp. Das Modell (z. B. S30 Pro statt S50) stimmt erst, wenn das echte Teleskop verbunden war.
- **Controls enabled** und **Allow power actions** sind standardmäßig aus. Schalte sie nur bewusst und zeitlich begrenzt ein, z. B. per Automation.
- Ab Firmware 7.18 brauchen Steuerbefehle den Interop-Key in seestar_alp. Ohne Key bleiben einige Gerätestatus-Felder `unknown`.

## Dashboard-Karte

[jaxzin/seestar-lovelace-card](https://github.com/jaxzin/seestar-lovelace-card): HACS → ⋮ → Benutzerdefinierte Repositories → Typ **Dashboard**.

Beispiel mit Sichtbarkeit, die Karte erscheint nur bei verbundenem Teleskop: [`homeassistant/seestar-card.yaml`](../homeassistant/seestar-card.yaml)

## siril-auto in HA

Mit `MQTT_ENABLED=1` legt [`siril-auto`](../siril) per Discovery das Gerät **Siril Auto-Stacking** an. Es hat zwei Sensoren:
- **Status**: `idle`, `fetching`, `processing` oder `error`
- **Letztes Ergebnis**: Ziel und Nacht, dazu als Attribute Frames und die Pfade zu JPG und FIT

Die Meldung „Letztes Ergebnis“ enthält auch `id` und `group`. Daraus entstehen die Galerie-Links: `<galerie>/img/<id>.jpg` für das Bild, `<galerie>/#<group>` für die Detailseite.

### Package Astro-Stacks (Benachrichtigungen)

[`homeassistant/packages/astro_stacks.yaml`](../homeassistant/packages/astro_stacks.yaml):

| Teil | Was |
|---|---|
| `sensor.astro_stacks_dieser_nacht` | Anzahl der Stacks seit 12 Uhr, Attribut `stacks` mit Ziel, Nacht, Filter, Frames und Galerie-Gruppe. Doppelte Meldungen nach einem HA-Neustart (retained) zählen nicht. |
| Push je Stack | Vorschaubild aus der Galerie. Tippen öffnet die Detailseite. Schalter: `input_boolean.astro_push_pro_stack` |
| Zusammenfassung am Morgen | um `input_datetime.astro_zusammenfassung_zeit`, nur wenn es Stacks gab; mit Hinweis, falls siril-auto einen Fehler meldet |
| Fehler-Push | wenn siril-auto länger als 1 min `error` meldet |

Vorher `http://astro.lan` (Galerie-Adresse) ersetzen. Die Empfänger kommen aus der KONFIGURATION in `astro_audit.yaml` – beide Packages gehören zusammen. Das Vorschaubild kommt aus `/img/` der Galerie, das ohne Passwort erreichbar ist ([06](06-galerie-hosting.md)). Uhrzeit und Push-Schalter einmal im UI setzen. Helfer ohne Startwert stehen nach dem ersten Laden auf aus bzw. 00:00.

## Astro-Audit (KI-Empfehlung → Automationen)

[`homeassistant/packages/astro_audit.yaml`](../homeassistant/packages/astro_audit.yaml) baut auf dem KI-Script mit AstroWeather auf (`ai_task.generate_data`). Das Script liefert zusätzlich **Stufe** und **Modus** als strukturierte Felder und schreibt das Ergebnis in Helfer. Bisher kam das Ergebnis nur als Benachrichtigung.

| Helfer | Inhalt |
|---|---|
| `input_boolean.astro_nacht_lohnt` | an, wenn Deep-Sky **oder** Milchstraße ideal, gut oder bedingt ist |
| `input_select.astro_stufe` / `astro_modus` | Stufe für Deep-Sky (ideal … abbruch) / bester Modus: deepsky, weitfeld, planetar, keiner |
| `input_select.astro_stufe_milchstrasse` | Stufe für Milchstraße/Weitwinkel, getrennt bewertet |
| `input_datetime.astro_mw_start` / `_ende` | Milchstraßen-Fenster: dunkel, Mond weg (falls er stört), eine Region ≥ 30° hoch |
| `input_text.astro_empfehlung` | Kurztext, z. B. „🌌 Ideal – Deepsky“ |
| `input_datetime.astro_fenster_start` / `_ende` | astronomische Dunkelheit; für Deep-Sky erst ab Monduntergang, wenn der Mond stört |
| `sensor.astro_taupunktabstand` | Temperatur minus Taupunkt. Die Quelle wählt der Sensor selbst: Sensor am Teleskop (später), dann Ecowitt im Garten, zuletzt das Wettermodell. Welche gerade aktiv ist, zeigt das Attribut `quelle`. |

**Modus weitfeld (Milchstraße):** Das Script rechnet für die astronomische Dunkelheit aus, wie hoch Milchstraßenzentrum, Schwan, Kassiopeia–Perseus und Fuhrmann–Orion maximal stehen (Standort aus `zone.home`), und gibt das der KI mit. Sie wählt `weitfeld`, wenn der Mond nicht stört, die Transparenz hoch und eine Region mindestens 35° hoch ist, und nennt Region und Uhrzeit im Bericht. Das Beobachtungsfenster beginnt dann wie bei Deep-Sky erst nach Monduntergang. Keine zusätzlichen Entities – nur eine weitere Option in `input_select.astro_modus`. Ausrüstung und Ablauf: [09](09-weitwinkel.md).

### Mehrere Teleskope und Handys

Oben im Package steht der Abschnitt **KONFIGURATION** (im Script `astro_benachrichtigen`). Dort trägst du alle Teleskope und alle Handys ein. Die Listen gelten für alle Automationen (YAML-Anker) und über das Script auch für die Meldungen aus `astro_stacks.yaml`.

```yaml
teleskope: &astro_teleskope
  - {id: s50, name: Seestar S50, prefix: seestar_s50, weitwinkel: false}
  - {id: s30, name: Seestar S30 Pro, prefix: seestar_s30, weitwinkel: true}
empfaenger: &astro_empfaenger
  - notify.mobile_app_handy
  - notify.mobile_app_handy_2
```

- `prefix` = Entity-Präfix in ha-seestar (z. B. `switch.<prefix>_dew_heater`). Geräte, die es (noch) nicht gibt oder die nicht verbunden sind, werden übersprungen.
- `id` kurz und ohne Unterstrich – sie steht in den Push-Aktionen.
- `weitwinkel: true` = das Gerät kann Milchstraße (Weitwinkelkamera, S30 Pro).

**Gleiche Nacht, zwei Ziele:** Die KI bewertet Deep-Sky und Milchstraße getrennt (zwei Stufen, zwei Fenster im Dashboard). Gestartet wird **nur per Knopf**, jedes Gerät hat eigene Knöpfe im Dashboard (S50: Session starten; S30 Pro: Milchstraße oder Deep-Sky). Der Knopfdruck ist die Bestätigung – es gibt keine Rückfrage per Push und keinen Start nach Zeit oder Bedingung. So funktioniert es auch, wenn nur einer von euch da ist oder beide gleichzeitig arbeiten.

Automationen:
- **Audit am Abend:** 30 min nach Sonnenuntergang
- **Session starten:** nur per Knopf im Dashboard. Je Gerät und Modus gibt es ein kleines Start-Script (`script.astro_start_s50`, `script.astro_start_s30_milchstrasse`, `script.astro_start_s30_deepsky`; neues Gerät = Block kopieren). Es ruft `script.astro_session_starten` mit Gerät (`teleskop: s50`) und Modus (`deepsky`/`weitfeld`) auf; das gibt die Steuerung dieses Geräts frei und enthält danach deinen eigenen Ablauf (Platzhalter, z. B. Plan im seestar_alp-Scheduler). Keine Automation ruft es auf.
- **GoTo:** Package [`astro_goto.yaml`](../homeassistant/packages/astro_goto.yaml) – je Gerät eine Auswahl `input_select.astro_ziel_<id>` (gut 50 Seestar-Ziele mit deutschen Namen plus Milchstraßen-Regionen) und ein Knopf **GoTo starten** (`script.astro_goto_<id>`). Das gemeinsame `script.astro_goto` (in `astro_audit.yaml`) gibt die Steuerung frei, schreibt Name, RA und Dec in die Text-Entities von ha-seestar (`text.<prefix>_goto_target/_goto_ra/_goto_dec`) und drückt `button.<prefix>_goto`. Steht das Ziel gerade unter 15°, bricht es mit einer Fehlermeldung ab. Der Goto-Knopf der seestar-lovelace-card ist im Dashboard auf dasselbe Script umgelenkt (`actions.goto`), nutzt also ebenfalls die Auswahl. Ziele ergänzen: `homeassistant/tools/make_goto_targets.py` (Koordinaten aus OpenNGC) oder von Hand in beiden Listen des Packages.
- **Stoppen / Parken:** nur per Knopf im Dashboard, je Gerät (`script.astro_stopp_s50`, `script.astro_parken_s50`, …), beide mit Rückfrage beim Antippen der Kachel. Ein „Drücken“-Knopf fehlt bewusst: Kachel-Funktionen können (noch) keine Rückfrage zeigen ([Feature Request](https://github.com/orgs/home-assistant/discussions/5263)). Beide rufen `script.astro_session_beenden` auf; gestoppt werden ein laufendes GoTo (`button.<prefix>_stop_goto`) und die Session (`button.<prefix>_stop`).
- **Steuerung sperren:** zum Fensterende, spätestens eine Stunde vor Sonnenaufgang, für alle Teleskope. Dabei gehen auch Power-Aktionen und Tauheizung aus.
- **Tauheizung:** mit Hysterese, an ab ≤ 3 K Abstand, aus ab ≥ 5 K – für jedes freigegebene Teleskop
- **Schutz (Regen, Wind, Tau):** einzige automatische Aktion (neben der Tauheizung), nur nachts, für alle verbundenen Teleskope. Auslöser: Ecowitt-Regen (Event-Regen steigt oder Regenrate > 0), Wettermodell, Böen > 35 km/h (2 min), Taupunktabstand < 1 K trotz Heizung (15 min). Mit `input_boolean.astro_schutz_parken` an werden die Geräte **geparkt** (dafür schaltet die Automation „Allow power actions“ ein), sonst nur gewarnt. Push an alle, höchstens alle 10 Minuten.

Vor dem Einsatz die KONFIGURATION anpassen. Packages aktivierst du in der `configuration.yaml` (eingerückt unter `homeassistant:`):

```yaml
homeassistant:
  packages: !include_dir_named packages
```

## Dashboard „Sternwarte“

[`homeassistant/dashboards/sternwarte.yaml`](../homeassistant/dashboards/sternwarte.yaml) ist ein Sections-Dashboard. `sensor.siril_auto_stacking_status` und das letzte Ergebnis erscheinen erst, wenn siril-auto mit `MQTT_ENABLED=1` gelaufen ist:

- **Heute Nacht und Bedingungen** (eine Spalte): Empfehlung und Beobachtungsfenster aus dem Astro-Audit, Stufe und Fenster für Deep-Sky und Milchstraße, Schalter Schutz-Parken, Audit per Knopf. Ist das Fenster der letzten Nacht vorbei, zeigt die Karte stattdessen, wann das nächste Audit kommt, und das letzte Ergebnis klein darunter; die Stufen-Kacheln sind tagsüber ausgeblendet. Darunter Taupunktabstand samt Quelle, Ecowitt, AstroWeather und Stundenvorhersage.
- **Seestar** (doppelt breit): Akku und Speicher als Badges, seestar-lovelace-card, wenn das Teleskop verbunden ist, sonst ein Warnhinweis; Steuerungsfreigabe, Tauheizung, Knopf **Session starten**
- **Variante 2:** [`sternwarte_v2.yaml`](../homeassistant/dashboards/sternwarte_v2.yaml) baut dasselbe fast nur aus Tile-Cards (mit Features: Schalter, Knöpfe, Auswahl, Trend-Graph) und Bildkarten, ohne seestar-card. Zusätzlich: Live-Vorschau (`camera.<präfix>_live_stacked_preview`), Session (Ziel, gestackt/verworfen, Belichtung, erkannte Sterne – nur während gestackt wird), Montierung (geparkt, Nachführung, GoTo, Höhe/Azimut), Gerät (Akku, Ladegerät, Speicher, Sensortemperatur, Firmware), die letzte Warnung des Seestar und das Bild des letzten Stacks (`image.astro_letzter_stack` aus `astro_stacks.yaml`). Mit Tabs: **Heute Nacht** (Empfehlung, Bedingungen, Geräte-Kacheln, die in die Tabs springen), **S50** und **Siril**. Zweites Seestar: `optional/seestar_s30_view.yaml` als weiterer Tab.
- **Meldungen aus den Seestar-Entities** (`astro_audit.yaml`, nur Push, es wird nichts geschaltet): Warnung des Geräts („Last alert“), **Kaum Sterne** (während gestackt wird seit 10 min unter 20 erkannten Sternen – Wolken/Tau), **Seestar prüfen** (verbunden mit Speicher ≥ 85 % oder Akku < 30 % ohne Ladegerät). Die Template-Entities suchen alle Geräte mit Präfix `seestar_*` selbst. Das Schutz-Parken überspringt Geräte, die schon geparkt sind.
- **Gesperrte Knöpfe ausgrauen:** Mit der Add-on-Option `gate_availability: true` (ha-seestar, derzeit im Fork) sind Knöpfe, Schalter und Zahlenwerte *nicht verfügbar*, solange **Steuerung freigegeben** aus ist (Parken/Ausschalten zusätzlich **Power-Aktionen erlauben**). Die Scripts in `astro_audit.yaml` geben die Steuerung selbst frei und warten danach bis zu 10 s, bis der Knopf verfügbar ist.
- **Seestar S30 Pro** und **Astro-Pi** sind optionale Abschnitte in [`homeassistant/dashboards/optional/`](../homeassistant/dashboards/optional): erst anhängen, wenn es die Geräte in HA gibt – sonst meldet z. B. Spook unbekannte Entities. Aufbau wie beim S50 (S30 Pro mit Knöpfen **Milchstraße starten** und **Deep-Sky starten**) bzw. nur Anzeige (Astro-Pi).
- **Nachverarbeitung:** Siril-Status, letzter Stack mit Gerät und Vorschaubild aus der Galerie, Stacks dieser Nacht, Link zur Astro-Galerie

Einfügen über den Raw-Konfigurationseditor eines neuen Dashboards. Vorher `seestar_s50`, die Galerie-Adresse und die Adresse des Astro-Pi anpassen.

## Ideen für Automationen

| Idee | Auslöser / Bedingung | Braucht Interop-Key |
|---|---|---|
| Beobachtungsnacht-Empfehlung | AstroWeather, astronomische Dämmerung (Sonne < −18°), Mond, Regen/Taupunkt | nein |
| Session-Benachrichtigungen | Stacking-Status, Frames, Plate-Solve-Fehler, *Last alert* | nein |
| Regen-Schutz | Regensensor → Push, bei HA-Session Stop/Park | ja |
| Tauheizung | Temperatur − Taupunkt < 2–3 K → *Dew heater* an | ja |
| Akku laden mit PV-Überschuss | smarte Steckdose am Ladegerät | nein |
| Steuerung nur im Zeitfenster freigeben | *Controls enabled* zur Dämmerung an, morgens aus | – |
| Geplante Nacht starten | *Run plan* zur Dämmerung, wenn die Empfehlung passt | ja |
| Außenlicht aus | während einer Session | nein |
| Nachverarbeitung | Seestar wieder im Heim-WLAN → siril-auto; Ergebnis → Push | nein |
| Speicher-Warnung | Seestar-Speicher > 80 % | nein |
