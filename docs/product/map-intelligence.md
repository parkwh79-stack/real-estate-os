# Map Intelligence

Updated: 2026-07-01

Map Intelligence is the project-level spatial view for REALYTICS. It connects
land ledger parcels, PNU, VWorld cadastral boundaries, official land price,
zoning overlays, market evidence, and risk context.

## Current Behavior

- `GET /projects/{id}/map` returns `realytics-map-intelligence-v1`.
- `parcelGeoJson` includes every stored parcel as a GeoJSON Feature.
- Parcels without a verified boundary remain `geometry: null`; the system does
  not invent coordinates.
- `resolve_geometry=true` fetches missing parcel boundaries from VWorld by PNU.
- `geometry_limit` controls the maximum number of PNU boundary lookups per
  request. The frontend map page requests `geometry_limit=150`, enough for the
  current 112-parcel pilot package.
- VWorld/PostGIS parcel geometry is cached through `parcel_geometry_cache` to
  avoid repeated external calls.

## 112-Parcel Selection UX

The project map page now supports large land-ledger packages:

- Select all parcels.
- Clear all parcels.
- Select only parcels with verified map geometry.
- Show only selected parcels.
- Fit the map to the selected parcel group.
- Inspect every parcel in a scrollable selector and a detail table.

This means a 112-parcel land ledger can be reviewed as one package while still
allowing parcel-by-parcel inspection.

## Layer Model

| Layer | Source |
| --- | --- |
| Parcel boundary | VWorld cadastral map / PostGIS `land_parcels` |
| Official land price | Public data / VWorld / manual evidence |
| Zoning | VWorld land-use plan overlay |
| Transactions and market price | MOLIT transaction API / Valuemap |
| Nearby supply | Marketability evidence |
| Risk | Risk Register |

## Data Policy

- PNU, official land price, and parcel boundary must be source-backed.
- Missing geometry is displayed as a data limitation, not fabricated.
- Registry and encumbrance evidence remain manual/licensed evidence unless a
  legally usable connector is added.
- Map output should be treated as spatial evidence support, not as a final legal
  title opinion.

## Next

- Add unresolved-parcel correction UI for parcels without PNU or boundary.
- Persist selected parcel groups as named review scopes.
- Connect selected parcel groups to Risk Register and Evidence Graph.
- Add radius-based market comparable search from selected parcel centroid.
