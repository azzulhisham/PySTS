from flask import Flask, jsonify, request
from flask_cors import CORS, cross_origin
from flask_swagger_ui import get_swaggerui_blueprint
from datetime import datetime, timedelta

import json
import jwt
import logging
import os

from polygons import anchorage_areas, all_polygons
from sts_detection import (
    detect_sts_in_anchorages,
)
from illegal_anchoring import detect_illegal_anchoring
from dark_vessels import detect_dark_vessels
from timelineplayback import get_vessel_activity_timeline, get_vessel_track_replay
from sanctions import query_ofac_vessels
from swagger_sample import apply_swagger_sample, is_swagger_referer


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

SWAGGER_URL = "/swagger"
API_URL = "/static/swagger.json"

swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={"app_name": "MANTIS API"},
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

# Credentials / JWT settings (override via environment variables)
USER_ID = os.environ.get("sts_user_id", "user@sts.my")
ACCESS_KEY = os.environ.get(
    "sts_access_key",
    "vZOODBrmB3cc0nvMiLwXtssAnchorageuj15dNSohbDgldkW_NI",
)
JWT_SECRET = os.environ.get("sts_jwt_secret", "admin-sts@pinc.my")
ALLOWED_ROLES = ["superadmin", "admin", "pinc-dev", "sts-usr"]


def authorize_user(token):
    """Validate Bearer JWT. Returns 0 on success, -1 on failure."""
    try:
        token = token.replace("Bearer", "").replace("bearer", "").strip() if token else "---"
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])

        expired = datetime.strptime(payload["expiredDate"], "%Y-%m-%d %H:%M:%S")
        if expired < datetime.now():
            return -1

        role = payload.get("role", "")
        if role in ALLOWED_ROLES and payload.get("id") == USER_ID:
            logging.info(f"[authorize_user] authorized: {payload}")
            return 0

        return -1
    except Exception as e:
        logging.info(f"[authorize_user] error: {e}")
        return -1


def is_swagger_request() -> bool:
    """True when Try it out is run from /swagger (Referer). Real clients get the full list."""
    referer = request.headers.get("Referer") or request.headers.get("Referrer") or ""
    return is_swagger_referer(referer)


def json_for_client(payload, list_keys: list[str], wrap_list_as: str | None = None):
    return apply_swagger_sample(
        payload,
        list_keys,
        from_swagger=is_swagger_request(),
        wrap_list_as=wrap_list_as,
    )


@app.route("/authentication/token", methods=["POST"])
@cross_origin()
def get_token():
    try:
        req = json.loads(request.data)

        token_payload = {
            "name": USER_ID,
            "id": req["userId"],
            "tenant": "sts",
            "role": "sts-usr",
            "expiredDate": (datetime.now() + timedelta(days=180)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        if req.get("accessKey") != ACCESS_KEY or req.get("userId") != USER_ID:
            return jsonify({"message": "Unauthorized"}), 401

        jwt_token = jwt.encode(token_payload, JWT_SECRET, algorithm="HS256")
        # PyJWT >= 2 returns str; older versions return bytes
        if isinstance(jwt_token, bytes):
            jwt_token = jwt_token.decode("utf-8")

        return jsonify({
            "accessToken": jwt_token,
            "expiredDate": token_payload["expiredDate"],
        }), 200
    except Exception as e:
        logging.error(f"[get_token] error: {e}")
        return jsonify({"message": "Server error"}), 500


@app.route("/mantis/polygons", methods=["GET"])
@cross_origin()
def get_all_polygons():
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    try:
        # Anchorage areas + restricted-limit polygon.
        # Frontend/curl: bare array. Swagger: wrapped + 20-row sample.
        return jsonify(json_for_client(all_polygons, ["polygons"], wrap_list_as="polygons")), 200
    except Exception as e:
        logging.error(f"[get_all_polygons] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/mantis/sts-activities", methods=["GET"])
@cross_origin()
def get_sts_activities():
    """
    Active (is_open) high-suspicion proximity pairs whose cluster centroid
    falls inside a parent / watch polygon (not an Excl hole). Returns only
    paired vessels.
    """
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    try:
        min_score = request.args.get("minSuspicionScore", type=float)
        kwargs = {}

        if min_score is not None:
            kwargs["min_suspicion_score"] = min_score

        result = detect_sts_in_anchorages(**kwargs)

        payload = {
            "minSuspicionScore": result["min_suspicion_score"],
            "maxDistanceM": result["max_distance_m"],
            "openHighScoreCount": result["open_high_score_count"],
            "inAnchorageClusterCount": result["in_anchorage_cluster_count"],
            "pairCount": result["pair_count"],
            "pairedVesselCount": result["paired_vessel_count"],
            "pairedVessels": result["paired_vessels_payload"],
            "pairs": result["pairs_payload"],
            "sanctionsMatchPairCount": result["sanctions_match_pair_count"],
            "sanctionsMatchVesselCount": result["sanctions_match_vessel_count"],
        }
        return jsonify(json_for_client(payload, ["pairs", "pairedVessels"])), 200
    
    except Exception as e:
        logging.error(f"[get_sts_activities] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/mantis/illegal-anchoring", methods=["GET"])
@cross_origin()
def get_illegal_anchoring():
    """
    Heuristic illegal-anchoring candidates from stopped/stale vessels:
    inside restricted-limit OR parent / watch polygons from polygons.py.
    Vessels inside an Excl carve-out (hole inside a parent polygon) are excluded.
    """
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    try:
        result = detect_illegal_anchoring()
        payload = {
            "ruleVersion": result["rule_version"],
            "shipTypeFilter": result["ship_type_filter"],
            "stoppedCandidateCount": result["stopped_candidate_count"],
            "illegalCount": result["illegal_count"],
            "byReason": result["by_reason"],
            "watchPolygonCount": result["watch_polygon_count"],
            "portLimitPolygonCount": result["port_limit_polygon_count"],
            "sanctionsMatchCount": result["sanctions_match_count"],
            "vessels": result["vessels_payload"],
        }
        return jsonify(json_for_client(payload, ["vessels"])), 200
    
    except Exception as e:
        logging.error(f"[get_illegal_anchoring] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/mantis/darkvessels", methods=["GET"])
@cross_origin()
def get_dark_vessels():
    """
    Suspected dark / AIS-transponder-off vessels from slow-move activities.
    Candidates are not dropped by polygon. When the last position is inside
    a polygon from polygons.py, polygonName is attached (Excl name preferred
    if the point is in a hole). Labels are heuristics — coverage exit is a
    competing explanation (see backend/mantis-detection.md).
    """
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    try:
        include_exit = request.args.get("includeCoverageExit", "true").lower() not in (
            "0", "false", "no",
        )
        result = detect_dark_vessels(include_coverage_exit=include_exit)
        payload = {
            "ruleVersion": result["rule_version"],
            "shipTypeFilter": result["ship_type_filter"],
            "minSilenceMinutes": result["min_silence_minutes"],
            "coverageExitDays": result["coverage_exit_days"],
            "includeCoverageExit": result["include_coverage_exit"],
            "candidateCount": result["candidate_count"],
            "byReason": result["by_reason"],
            "byConfidence": result["by_confidence"],
            "sanctionsMatchCount": result["sanctions_match_count"],
            "vessels": result["vessels_payload"],
        }

        return jsonify(json_for_client(payload, ["vessels"])), 200
    
    except Exception as e:
        logging.error(f"[get_dark_vessels] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/mantis/sanctions", methods=["GET"])
@cross_origin()
def get_sanctions_list():
    """
    OFAC vessel list from pnav (SDN + consolidated non-SDN if loaded).

    Query imo and/or mmsi to search. If neither is set, return the full
    vessel list. Requests from Swagger UI are capped at 20 sample rows.
    """
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    try:
        result = query_ofac_vessels(
            imo=request.args.get("imo"),
            mmsi=request.args.get("mmsi"),
        )
        payload = {
            "imo": result["imo"],
            "mmsi": result["mmsi"],
            "vessels": result["vessels"],
        }
        return jsonify(json_for_client(payload, ["vessels"])), 200
    except Exception as e:
        logging.error(f"[get_sanctions_list] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/mantis/vessel-timeline", methods=["GET"])
@cross_origin()
def get_vessel_timeline():
    """
    Derived activity/events for one vessel between two datetimes.

    Sources: zone visits, restricted zones, stop/slow-move activities,
    and static identity changes (PostgreSQL).
    """
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    mmsi = request.args.get("mmsi", type=int)
    date_from = request.args.get("from")
    date_to = request.args.get("to")

    if not mmsi or not date_from or not date_to:
        return jsonify({
            "message": "Query parameters mmsi, from, and to are required",
        }), 400

    try:
        payload = get_vessel_activity_timeline(mmsi, date_from, date_to)
        return jsonify(json_for_client(payload, ["events"])), 200
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        logging.error(f"[get_vessel_timeline] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/mantis/vessel-track", methods=["GET"])
@cross_origin()
def get_vessel_track():
    """
    Full AIS position track for map replay between two datetimes (ClickHouse).
    """
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    mmsi = request.args.get("mmsi", type=int)
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    include_class_b = request.args.get("includeClassB", "false").lower() not in (
        "0", "false", "no",
    )

    if not mmsi or not date_from or not date_to:
        return jsonify({
            "message": "Query parameters mmsi, from, and to are required",
        }), 400

    try:
        payload = get_vessel_track_replay(
            mmsi,
            date_from,
            date_to,
            include_class_b=include_class_b,
        )
        return jsonify(json_for_client(payload, ["track"])), 200
    except ValueError as e:
        return jsonify({"message": str(e)}), 400
    except Exception as e:
        logging.error(f"[get_vessel_track] error: {e}")
        return jsonify({"message": "Internal server error"}), 500


@app.route("/", methods=["GET"])
@cross_origin()
def health():
    if authorize_user(request.headers.get("Authorization")) < 0:
        return jsonify({"message": "Unauthorized"}), 401

    return jsonify({
        "status": "ok",
        "service": "MANTIS API",
        "polygonCount": len(all_polygons),
        "swagger": SWAGGER_URL,
    }), 200


if __name__ == "__main__":
    # Prefer gunicorn for deployment:
    #   gunicorn -c gunicorn_config.py main:app
    # Flask development server (local debugging only):
    port = int(os.environ.get("py_flask_port", 8080))
    app.run(debug=False, host="0.0.0.0", port=port)
