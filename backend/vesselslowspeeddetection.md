# Vessel Slow Speed Detection Backend

This backend process detects vessels that are slowing down, stopping, leaving a stopped location, or possibly turning off their AIS transponder while slowing down.

The main objective is to identify vessels that may try to go dark by switching off AIS before they are fully confirmed as stopped.

## Source Data

The process reads AIS position records from `public.ais_position` using `get_ais_position_data()`.

The current query loads AIS records from the last 6 days and orders them by timestamp. Each AIS row contains vessel identity, position, speed, course, navigation status, and timestamp information.

Important fields from AIS data:

- `mmsi`: vessel identifier.
- `ts`: AIS position timestamp.
- `longitude` and `latitude`: vessel position.
- `sog`: speed over ground.
- `cog`: course over ground.
- `navStatus` and `navStatusDesc`: AIS navigation status.

## Output Table

Detected activities are stored in `public.ais_vesselslowmoveactivities`.

Important fields:

- `ts`: first detected slow-speed timestamp.
- `mmsi`: vessel identifier.
- `longitude` and `latitude`: first detected position.
- `curlongitude` and `curlatitude`: latest known position.
- `sog` and `cog`: first detected speed and course.
- `cursog` and `curcog`: latest known speed and course.
- `rowcount`: count used to confirm slow movement or stop behavior.
- `rowcount2`: count used during high-speed exit handling.
- `distance`: distance between the latest AIS point and the previously stored point.
- `tscurrent`: latest AIS timestamp processed for the activity.
- `tsstop`: timestamp when the vessel is considered stopped or suspected dark.
- `tsout`: timestamp when the vessel is considered to have left the location.

## Processing Flow

The backend runs continuously in a loop. Every cycle, it performs these steps:

1. Fetch recent AIS position data.
2. Split vessels into low-speed and high-speed groups.
3. Process low-speed vessels into slow-move activities.
4. Mark stale slow-speed activities as suspected stopped or dark.
5. Process high-speed vessels to detect vessels leaving the location.
6. Sleep for 20 seconds, then repeat.

## Low-Speed Detection

A vessel is treated as low speed when:

```sql
sog <= 3.0
```

For each low-speed AIS position, the process checks whether there is already an open activity for the same `mmsi` where `tsout IS NULL`.

If no open activity exists, a new activity is inserted.

If an open activity exists, the process only updates it when the incoming AIS timestamp is newer than the stored `tscurrent`. This prevents older AIS rows from increasing `rowcount` again.

The process calculates the distance between the new AIS position and the stored current position. If the position changed, `rowcount` is incremented.

## Confirmed Stop Detection

A vessel is considered confirmed stopped when:

- the activity is still open;
- the vessel continues sending AIS positions;
- `rowcount >= 30`;
- the distance between current and previous position is less than 30 meters.

When this happens, `tsstop` is set to the current AIS timestamp.

This means the vessel was still transmitting AIS when the system confirmed that it had stopped.

## Suspected Transponder-Off Detection

The process also detects vessels that may have turned off their AIS transponder before reaching confirmed stop status.

This is handled by checking stale open records after low-speed processing.

A vessel is treated as suspected stopped or dark when:

- `tsstop IS NULL`;
- `tsout IS NULL`;
- `tscurrent` is not null;
- `tscurrent` is older than `STALE_TRANSPONDER_MINUTES`;
- `rowcount >= STALE_TRANSPONDER_MIN_ROWCOUNT`.

The current values are:

```python
STALE_TRANSPONDER_MINUTES = 30
STALE_TRANSPONDER_MIN_ROWCOUNT = 10
```

When these conditions are met, the process sets:

```sql
tsstop = tscurrent
```

This means the vessel did not provide enough AIS updates to be confirmed stopped, but it was already in a slow-speed candidate state and then disappeared from AIS.

## Estimated Dark-Stop Location

The Python file includes an `estimate_latlng()` helper that can project a possible stop location from the vessel's last known AIS position and course over ground (`cog`).

The current helper uses a fixed projected distance of `540m`. This distance assumes the vessel was travelling at about `3 knots` when the projection starts.

If the projection is scaled linearly by speed, the estimated distance becomes:

- `3 knots`: about `540m`.
- `2 knots`: about `360m`.
- `1 knot`: about `180m`.
- Below `1 knot`: less than `180m`; for example, `0.5 knot` is about `90m`.

A simple speed-aware projection could therefore calculate distance as:

```python
d = 540.0 * (sog / 3.0)
```

Another interpretation is to model the vessel as decelerating to a stop with the same deceleration rate. In that case, stopping distance scales with the square of speed:

- `3 knots`: about `540m`.
- `2 knots`: about `240m`.
- `1 knot`: about `60m`.
- Below `1 knot`: less than `60m`; for example, `0.5 knot` is about `15m`.

The non-linear deceleration projection can be calculated as:

```python
d = 540.0 * (sog / 3.0) ** 2
```

The linear version is easier to explain operationally, while the non-linear version is closer to a constant-deceleration stopping-distance model. The better choice depends on real vessel behavior, AIS update frequency, and the vessel type.

This should be interpreted as an estimated dark-stop location, not an actual confirmed stop position. The vessel may turn, drift, anchor, or slow down at a different rate after the last AIS message. The original last-known AIS latitude and longitude should therefore be preserved separately from any estimated stop latitude and longitude.

## How To Interpret Detection Results

Use `tsstop`, `tsout`, and `rowcount` together when interpreting the detection result.

- `tsstop IS NULL` and `tsout IS NULL`: the vessel is still being monitored.
- `tsstop IS NOT NULL` and `rowcount >= 30`: the vessel is confirmed stopped while still transmitting AIS.
- `tsstop IS NOT NULL` and `rowcount < 30`: the vessel is suspected to have turned off its transponder while slowing or preparing to stop.
- `tsout IS NOT NULL`: the vessel has moved out or resumed high-speed movement.

This interpretation is important because `tsstop` is used for both confirmed stop and suspected dark-stop cases.

## High-Speed Exit Detection

A vessel is treated as high speed when:

```sql
sog > 3.0
```

The process checks open slow-speed activities against vessels that now appear in the high-speed group.

If a matching high-speed AIS record is found and the distance from the stored activity position is at least 100 meters, `tsout` can be updated to indicate that the vessel has left the location.

## Operational Notes

- The process runs continuously when the file is executed directly.
- The loop interval is 20 seconds.
- DuckDB spatial functions are used to calculate distance between positions.
- PostgreSQL stores both AIS source data and detected slow-speed activity records.
- Current thresholds are hardcoded in the Python file and should be tuned based on AIS update frequency, vessel behavior, and operational area.

## Current Limitations

- `tsstop` has two meanings: confirmed stopped and suspected dark-stop. A separate field such as `tsdark`, `darkflag`, or `detectiontype` would make the result clearer.
- The AIS query reads the last 6 days of data every cycle. A timestamp or ID watermark would make processing more efficient.
- `rowcount` depends on AIS reporting frequency. A time-based rule may be more stable across vessels.
- Position jitter can affect distance and row counting, especially when a vessel is nearly stationary.
