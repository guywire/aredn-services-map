# Sharing between copies on the mesh

Copies of the AREDN Services Map (and SignalMatrix / ECH, which runs the same
code) talk to each other over the mesh so the mesh is checked once, not once
per copy, and so a box with no internet still gets a full map.

The exchange is plain HTTP over the mesh, organised by **layer**. Version 1.

## Finding each other

* Every copy advertises itself on its AREDN node as a service named
  **AREDN Services Map** (a link to its page). Others find it in the mesh-wide
  service list they already read.
* `sharing.peers` in the config adds copies by URL.
* Requests go to the copy's IP, with its name in the `Host` header.

## `GET /peer/v1/info`

```json
{"app": "aredn-services-map", "proto": 1, "version": "0.2.0",
 "station": "N0CALL-map", "key": "<Ed25519 public key, base64>",
 "layers": {
   "services": {"scheme": "octet2/4",
                "regions": {"1": {"scanned_at": 1795520996.6, "station": "N0CALL-map", "count": 378}}},
   "tiles":    {"max_zoom": 19, "offline_file": false}}}
```

A copy lists only the layers it serves. Unknown layers are ignored, so new
layers can be added without breaking older copies.

## Layer `services`

`GET /peer/v1/services/region/N` returns one region's last result, signed:

```json
{"payload": "<JSON text>", "sig": "<Ed25519 signature of the payload text, base64>"}
```

The payload holds `app`, `proto`, `layer: "services"`, `scheme`, `region`,
`scanned_at`, `scanner {station, key}` and `services`: one entry per service,
identified by `id` (a hash of the advertising node's IP, the service name and
its link, so every copy computes the same id from its own list) with only the
measurements: status, kind, software, view, ms, open ports and apps, feed,
and the node's name and location. Names and links are never taken from a
peer.

A copy uses another's result only after the checks in the README ("Sharing
and regions"): signature and pinned key, age under 4 hours, nothing outside
its own service list, 90% coverage, and a random re-check that mostly agrees.
A relayed result keeps its original signature.

Regions are by the second octet of the advertising node's address, four of
them; one is checked every 6 hours. `scheme` names the rule, so a future
change (say, eight regions on a much bigger mesh) is a new scheme, not a
silent mismatch.

## Layer `tiles`

`GET /peer/v1/tiles/Z/X/Y.png` returns a map tile **only from the copy's
offline tile file or its cache**, never fetched from the internet on the
asker's behalf (that would turn every copy with internet into an open proxy
for the tile server). A copy without internet tries, in order: its offline
file, its cache, the internet, then up to four copies on the mesh. A tile from
a peer must be a PNG, JPEG or WebP image under 600 KB.

## Planned layers

All of these follow the same pattern: an entry in `/peer/v1/info`, a path
under `/peer/v1/<layer>/`, a signed payload naming its layer, and checks a
receiving copy can make itself (a spot-check against the source where the
data is re-checkable, a plausibility check where it isn't).

| Layer | What | Source on the mesh | Freshness | How a receiver checks it |
|---|---|---|---|---|
| `adsb` | aircraft positions | the mesh's ADS-B receivers (`aircraft.json` feeds the services layer finds) | seconds; snapshot every 30-60 s | the feed URL is in its own services list; fetch one sample feed itself |
| `ais` | ship positions | AIS receivers' `ships.json` | 1-5 min | same as ADS-B |
| `meshphone` | PBX up/down and trunk latency | IAX2 POKE results | daily | spot-check a sample of PBXs |
| `aprs` | APRS stations heard on RF | a copy with a radio / igate | minutes | plausibility only (positions, callsign format); marked "heard by" |
| `weather` | readings from mesh weather stations | weather services in the services layer | 10-30 min | fetch a sample station itself |
| `alerts` | NWS alerts cached while online | a copy with internet | when issued | signed by the copy; shown with its source and time |

The aim is a mesh-wide picture ("god's-eye view") that any copy can show to
anyone on the mesh, with or without internet: whoever reaches one copy sees
what all of them know. Kept simple: each layer is one small, signed snapshot a
copy can fetch in one request, not a stream.
