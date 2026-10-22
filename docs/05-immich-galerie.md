# Immich und Astro-Galerie

## Rollen

| Version | Woher | Immich | Galerie |
|---|---|---|---|
| App-Stack | Seestar-App → Handy-Backup | ja (Schnellansicht) | – |
| Seestar-Auto-Stack | Gerät → `raw/seestar/` | nein (Duplikat zum App-Stack) | – |
| Siril-Stack (Nacht / Master) | siril-auto → `export/` | ja | ja |
| Beschriftet (`plot-constellations`) | siril-auto | ja | – |

- `export/` wird **read-only** in den Immich-LXC eingebunden und als **External Library** angelegt. Es entstehen keine Kopien.
- Das Aufnahmedatum setzt exiftool ins JPG, damit Immich das Bild in die richtige Nacht einsortiert.
- Geplant: App- und Siril-Version zu einem **Immich-Stack** gruppieren, mit der Siril-Version als Titelbild. Zugeordnet wird über die Immich-API anhand von Aufnahmezeit und Dateiname.

## Astro-Galerie

Prototyp: [`gallery/prototype.html`](../gallery/prototype.html). Er ist eigenständig, die Bilder sind simuliert, die Objektdaten real.

- Übersicht: Kennzahlen, Beobachtungskalender (Filter, Mondphasen), Kacheln mit Filter und Sortierung
- Detailseite: Bild mit SVG-Overlay, das beim Mouseover die Annotationen zeigt, dazu Infokarten, RA/Dec-Gitter, Kompass und Maßstab. Am Rand ein Hinweis auf Objekte außerhalb des Bildfelds.
- Rotlicht-Modus für die Nachtsicht

Später liefert siril-auto pro Stack eine JSON-Datei mit Nächten, Frames, Plate-Solving und den Objekten aus `conesearch`. Die Galerie wird daraus statisch gebaut und per nginx ausgeliefert.

## Ältere Bilder nachträglich

| Quelle | Weg |
|---|---|
| Siril-FIT | Batch-`platesolve` |
| Seestar-FIT vom Gerät | RA/Dec steht im Header, damit schnell gelöst |
| App-JPG in Immich | per API laden, dann `solve-field` mit Skalenhinweis (≈ 2,39″/px), danach Overlay bzw. beschriftete Version |

Für astrometry.net brauchst du lokale Indexdateien für ein Bildfeld von etwa 0,7–1,3°.
