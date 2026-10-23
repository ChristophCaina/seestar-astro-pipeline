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

Vorher `http://astro.lan` (Galerie-Adresse) und `notify.mobile_app_handy` ersetzen. Das Vorschaubild lädt nur, wenn das Handy die Galerie erreicht, also im Heimnetz oder über Tailscale ([04](04-remote-access.md)). Uhrzeit und Push-Schalter einmal im UI setzen. Helfer ohne Startwert stehen nach dem ersten Laden auf aus bzw. 00:00.

## Astro-Audit (KI-Empfehlung → Automationen)

[`homeassistant/packages/astro_audit.yaml`](../homeassistant/packages/astro_audit.yaml) baut auf dem KI-Script mit AstroWeather auf (`ai_task.generate_data`). Das Script liefert zusätzlich **Stufe** und **Modus** als strukturierte Felder und schreibt das Ergebnis in Helfer. Bisher kam das Ergebnis nur als Benachrichtigung.

| Helfer | Inhalt |
|---|---|
| `input_boolean.astro_nacht_lohnt` | an bei Stufe ideal, gut oder bedingt |
| `input_select.astro_stufe` / `astro_modus` | ideal … abbruch / deepsky, weitfeld, planetar, keiner |
| `input_text.astro_empfehlung` | Kurztext, z. B. „🌌 Ideal – Deepsky“ |
| `input_datetime.astro_fenster_start` / `_ende` | astronomische Dunkelheit; für Deep-Sky erst ab Monduntergang, wenn der Mond stört |
| `input_boolean.astro_automatik` | Hauptschalter für die Automationen |
| `sensor.astro_taupunktabstand` | Temperatur minus Taupunkt. Die Quelle wählt der Sensor selbst: Sensor am Teleskop (später), dann Ecowitt im Garten, zuletzt das Wettermodell. Welche gerade aktiv ist, zeigt das Attribut `quelle`. |

**Modus weitfeld (Milchstraße):** Das Script rechnet für die astronomische Dunkelheit aus, wie hoch Milchstraßenzentrum, Schwan, Kassiopeia–Perseus und Fuhrmann–Orion maximal stehen (Standort aus `zone.home`), und gibt das der KI mit. Sie wählt `weitfeld`, wenn der Mond nicht stört, die Transparenz hoch und eine Region mindestens 35° hoch ist, und nennt Region und Uhrzeit im Bericht. Das Beobachtungsfenster beginnt dann wie bei Deep-Sky erst nach Monduntergang. Keine zusätzlichen Entities – nur eine weitere Option in `input_select.astro_modus`. Ausrüstung und Ablauf: [09](09-weitwinkel.md).

Automationen:
- **Audit am Abend:** 30 min nach Sonnenuntergang
- **Session bestätigen:** Zum Fensterbeginn (Nacht lohnt, Automatik an, Seestar verbunden) kommt eine Push-Nachricht mit **Session starten / Nur freigeben / Heute nicht**. Erst nach dem Tippen wird die Steuerung freigegeben und bei „Session starten“ `script.astro_session_starten` aufgerufen (Platzhalter, mit eigenem Ablauf füllen, z. B. Plan im seestar_alp-Scheduler). Ohne Antwort in 60 min bleibt alles gesperrt. **Nichts startet ohne Bestätigung.**
- **Steuerung sperren:** zum Fensterende, spätestens eine Stunde vor Sonnenaufgang. Dabei gehen auch Power-Aktionen und Tauheizung aus.
- **Tauheizung:** mit Hysterese, an ab ≤ 3 K Abstand, aus ab ≥ 5 K
- **Schutz (Regen, Wind, Tau):** einzige Aktion ohne Rückfrage, nur nachts und bei verbundenem Seestar. Auslöser: Ecowitt-Regen (Event-Regen steigt oder Regenrate > 0), Wettermodell, Böen > 35 km/h (2 min), Taupunktabstand < 1 K trotz Heizung (15 min). Mit `input_boolean.astro_schutz_parken` an wird das Seestar **geparkt** (dafür schaltet die Automation „Allow power actions“ ein), sonst nur gewarnt. Push mit hoher Priorität, höchstens alle 10 Minuten.

Vor dem Einsatz musst du `seestar_s50` (Entity-Präfix) und `notify.mobile_app_handy` per Suchen und Ersetzen anpassen. Packages aktivierst du in der `configuration.yaml` mit `homeassistant: packages: !include_dir_named packages`.

## Dashboard „Sternwarte“

[`homeassistant/dashboards/sternwarte.yaml`](../homeassistant/dashboards/sternwarte.yaml) ist ein Sections-Dashboard:

- **Heute Nacht und Bedingungen** (eine Spalte): Empfehlung und Beobachtungsfenster aus dem Astro-Audit, Hinweis bei einer Milchstraßen-Nacht (Modus `weitfeld`), Stufe, „lohnt sich“, Schalter für Automatik und Schutz-Parken, **Session starten** und Audit – beide nur per Knopf mit Rückfrage. Darunter Taupunktabstand samt Quelle, Ecowitt, AstroWeather und Stundenvorhersage.
- **Seestar** (doppelt breit): Akku und Speicher als Badges, seestar-lovelace-card, wenn das Teleskop verbunden ist, sonst ein Warnhinweis; Steuerungsfreigabe, Tauheizung
- **Seestar S30 Pro**: gleicher Aufbau, nur sichtbar, solange das zweite Seestar verbunden ist
- **Nachverarbeitung:** Siril-Status, letzter Stack mit Gerät und Vorschaubild aus der Galerie, Stacks dieser Nacht, Link zur Astro-Galerie
- **Astro-Pi** (iEXOS-100 bzw. NexStar GT mit EOS 6D; nur sichtbar, wenn der Pi per MQTT gemeldet ist): Montierung, Ziel, Serie, HFR, Speicher, letzte Aufnahme und Link zur Web-App – nur Anzeige, gesteuert wird in der App

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
