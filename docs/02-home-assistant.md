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

Eine Beispiel-Automation, die bei neuem Ergebnis eine Push-Nachricht schickt: [`homeassistant/automation-siril-ergebnis.yaml`](../homeassistant/automation-siril-ergebnis.yaml)

## Astro-Audit (KI-Empfehlung → Automationen)

[`homeassistant/packages/astro_audit.yaml`](../homeassistant/packages/astro_audit.yaml) baut auf dem KI-Script mit AstroWeather auf (`ai_task.generate_data`). Das Script liefert zusätzlich **Stufe** und **Modus** als strukturierte Felder und schreibt das Ergebnis in Helfer. Bisher kam das Ergebnis nur als Benachrichtigung.

| Helfer | Inhalt |
|---|---|
| `input_boolean.astro_nacht_lohnt` | an bei Stufe ideal, gut oder bedingt |
| `input_select.astro_stufe` / `astro_modus` | ideal … abbruch / deepsky, planetar, keiner |
| `input_text.astro_empfehlung` | Kurztext, z. B. „🌌 Ideal – Deepsky“ |
| `input_datetime.astro_fenster_start` / `_ende` | astronomische Dunkelheit; für Deep-Sky erst ab Monduntergang, wenn der Mond stört |
| `input_boolean.astro_automatik` | Hauptschalter für die Automationen |
| `sensor.astro_taupunktabstand` | Temperatur minus Taupunkt |

Automationen:
- **Audit am Abend:** 30 min nach Sonnenuntergang
- **Steuerung freigeben:** zum Fensterbeginn, wenn die Nacht lohnt, die Automatik an ist und das Seestar verbunden ist. Dazu kommt eine Push-Nachricht.
- **Steuerung sperren:** zum Fensterende, spätestens eine Stunde vor Sonnenaufgang. Dabei gehen auch Power-Aktionen und Tauheizung aus.
- **Tauheizung:** mit Hysterese, an ab ≤ 3 K Abstand, aus ab ≥ 5 K
- **Regenwarnung:** Push mit hoher Priorität. Automatisches Parken ist vorbereitet, aber auskommentiert.

Vor dem Einsatz musst du `seestar_s50` (Entity-Präfix) und `notify.mobile_app_handy` per Suchen und Ersetzen anpassen. Packages aktivierst du in der `configuration.yaml` mit `homeassistant: packages: !include_dir_named packages`.

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
