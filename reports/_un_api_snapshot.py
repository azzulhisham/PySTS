#!/usr/bin/env python3
"""Capture MANTIS API endpoint counts/timings for UN deploy verify."""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8085"
AUTH_BODY = json.dumps(
    {
        "userId": "user@sts.my",
        "accessKey": "vZOODBrmB3cc0nvMiLwXtssAnchorageuj15dNSohbDgldkW_NI",
    }
).encode()


def token() -> str:
    req = urllib.request.Request(
        BASE + "/authentication/token",
        data=AUTH_BODY,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.load(urllib.request.urlopen(req, timeout=30))["accessToken"]


def get(path: str, headers: dict) -> tuple[dict, float]:
    t0 = time.time()
    req = urllib.request.Request(BASE + path, headers=headers)
    data = json.load(urllib.request.urlopen(req, timeout=180))
    return data, round(time.time() - t0, 3)


def slim(name: str, data: dict, sec: float) -> dict:
    out = {k: v for k, v in data.items() if not isinstance(v, (list, dict))}
    out["_sec"] = sec
    if "vessels" in data and isinstance(data["vessels"], list):
        vessels = data["vessels"]
        out["vessel_count"] = len(vessels)
        out["listNames"] = sorted({v.get("listName") for v in vessels if v.get("listName")})
        out["un_count"] = sum(1 for v in vessels if v.get("listName") == "UN")
        out["sdn_count"] = sum(1 for v in vessels if v.get("listName") == "SDN")
        out["cons_count"] = sum(1 for v in vessels if v.get("listName") == "CONS")
        if name == "sanctions":
            un = [v for v in vessels if v.get("listName") == "UN"]
            out["un_sample"] = [
                {"vesselName": v.get("vesselName"), "imo": v.get("imo"), "listName": v.get("listName")}
                for v in un[:5]
            ]
    for key in ("cases", "activities", "events", "conflicts", "groups", "items", "vessels"):
        if key in data and isinstance(data[key], list) and key != "vessels":
            out[f"{key}_count"] = len(data[key])
    # identity-conflict shape
    if "clusters" in data and isinstance(data["clusters"], list):
        out["clusters_count"] = len(data["clusters"])
    if name == "overview":
        out["keys"] = sorted(data.keys())
        for k, v in data.items():
            if isinstance(v, list):
                out[f"{k}_count"] = len(v)
            elif isinstance(v, dict) and "count" in v:
                out[f"{k}_count"] = v.get("count")
    # sample sanctions fields on first STS activity if present
    acts = data.get("activities") or data.get("cases") or []
    if acts and isinstance(acts, list):
        sample = acts[0]
        out["sanctions_field_sample"] = {
            k: sample.get(k)
            for k in (
                "sanctionsMatch",
                "matchConfidence",
                "sanctionsList",
                "listNames",
            )
            if k in sample or True
        }
        # nested vesselA
        for side in ("vesselA", "vesselB"):
            if isinstance(sample.get(side), dict):
                out["sanctions_field_sample"][side] = {
                    k: sample[side].get(k)
                    for k in ("sanctionsMatch", "matchConfidence", "sanctionsList", "listNames")
                }
    return out


def main() -> int:
    out_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/un_api_snapshot.json"
    headers = {"Authorization": "Bearer " + token()}
    result = {}
    endpoints = [
        ("sanctions", "/mantis/sanctions"),
        ("sts", "/mantis/sts-activities"),
        ("sts_review", "/mantis/sts-review?page=1&pageSize=100"),
        ("illegal", "/mantis/illegal-anchoring"),
        ("dark", "/mantis/darkvessels"),
        ("identity", "/mantis/identity-conflict"),
        ("overview", "/mantis/overview"),
    ]
    for name, path in endpoints:
        try:
            data, sec = get(path, headers)
            result[name] = slim(name, data, sec)
            print("OK", name, sec, flush=True)
        except Exception as exc:  # noqa: BLE001
            result[name] = {"error": str(exc)}
            print("ERR", name, exc, flush=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, default=str)
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
