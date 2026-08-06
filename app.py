"""
Databricks App boilerplate:
- Serves a small Flask API
- Reads/writes to Lakebase (Databricks-managed Postgres) via lakebase.py
- Pulls data from the Massive API via massive_client.py and syncs it into Lakebase

Run locally:
    python app.py
Deploy as a Databricks App using app.yaml.
"""

import logging
import os
import re

import requests
from databricks.sdk import WorkspaceClient
from flask import Flask, jsonify, render_template, request

import lakebase
from massive_client import MassiveClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("massive-app")

app = Flask(__name__)
_w = WorkspaceClient()

TABLE_NAME = os.environ.get("MASSIVE_TABLE_NAME", "massive_records")
WATCHLIST_TABLE_NAME = os.environ.get("WATCHLIST_TABLE_NAME", "watchlist")

# Basic stock ticker shape check: 1-10 uppercase letters, with an optional
# ".X" or ".XX" share-class suffix (e.g. "BRK.B").
_TICKER_RE = re.compile(r"^[A-Z]{1,10}(\.[A-Z]{1,2})?$")


def ensure_table():
    """Create the destination table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id TEXT PRIMARY KEY,
            payload JSONB NOT NULL,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def ensure_watchlist_table():
    """Create the watchlist table in Lakebase if it doesn't exist yet."""
    lakebase.run_write(
        f"""
        CREATE TABLE IF NOT EXISTS {WATCHLIST_TABLE_NAME} (
            symbol TEXT NOT NULL,
            email TEXT NOT NULL,
            latest_price NUMERIC,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (symbol, email)
        )
        """
    )


def _current_user_email() -> str:
    """
    Resolve the current user's email so the watchlist can be personalized.

    Databricks Apps inject the logged-in user's identity via the
    X-Forwarded-Email header on every request. Fall back to the Databricks
    SDK's current_user API for local development where that header isn't set.
    """
    header_email = request.headers.get("X-Forwarded-Email")

    if header_email:
        return header_email

    return _w.current_user.me().user_name


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.errorhandler(Exception)
def handle_exception(err):
    """
    Ensure unhandled errors return JSON rather than an HTML error page.
    """
    logger.exception("Unhandled exception while processing request")

    status_code = getattr(err, "code", 500)

    if not isinstance(status_code, int):
        status_code = 500

    return jsonify({"error": str(err)}), status_code


@app.route("/")
def index():
    """Render the stock watchlist interface."""
    return render_template("index.html")


@app.route("/records")
def list_records():
    """Read records already synced into Lakebase."""
    limit = int(request.args.get("limit", 100))

    rows = lakebase.run_query(
        f"""
        SELECT id, payload, synced_at
        FROM {TABLE_NAME}
        ORDER BY synced_at DESC
        LIMIT %s
        """,
        (limit,),
    )

    return jsonify(rows)


@app.route("/sync", methods=["POST"])
def sync_from_massive():
    """
    Pull data from the Massive API and upsert it into Lakebase.
    """
    ensure_table()

    client = MassiveClient()

    if request.is_json:
        path = request.json.get("path", "/records")
    else:
        path = "/records"

    batch_size = int(request.args.get("batch_size", 500))

    batch = []
    total = 0

    for item in client.paginated_get(path):
        batch.append(item)

        if len(batch) >= batch_size:
            total += _upsert_batch(batch)
            batch = []

    if batch:
        total += _upsert_batch(batch)

    return jsonify({"synced": total})


@app.route("/watchlist", methods=["GET"])
def get_watchlist():
    """
    Return the current user's watchlist with the latest stored prices.
    """
    ensure_watchlist_table()

    email = _current_user_email()

    rows = lakebase.run_query(
        f"""
        SELECT
            symbol,
            email,
            latest_price,
            updated_at
        FROM {WATCHLIST_TABLE_NAME}
        WHERE email = %s
        ORDER BY symbol ASC
        """,
        (email,),
    )

    return jsonify(rows)


@app.route("/watchlist", methods=["POST"])
def add_to_watchlist():
    """
    Fetch the latest price for one stock symbol from Massive,
    then insert/update that symbol in Lakebase.
    """
    ensure_watchlist_table()

    if request.is_json:
        symbol = request.json.get("symbol", "")
    else:
        symbol = request.form.get("symbol", "")

    symbol = symbol.strip().upper() if isinstance(symbol, str) else ""

    # Validate ticker format before calling Massive
    if not symbol or not _TICKER_RE.match(symbol):
        return jsonify({
            "error": f"Invalid ticker symbol: {symbol!r}"
        }), 400

    client = MassiveClient()

    try:
        data = client.get_latest_price(symbol)

    except requests.HTTPError as e:
        status = (
            e.response.status_code
            if e.response is not None
            else None
        )

        body = (
            e.response.text
            if e.response is not None
            else str(e)
        )

        logger.error(
            "Massive API request failed for symbol=%s status=%s body=%s",
            symbol,
            status,
            body,
        )

        return jsonify({
            "error": "Massive API request failed",
            "symbol": symbol,
            "status": status,
        }), 400

    price = _extract_latest_price(data)

    if price is None:
        logger.warning(
            "No usable price returned from Massive for symbol=%s response=%s",
            symbol,
            data,
        )

        return jsonify({
            "error": f"No price data available for ticker: {symbol}"
        }), 400

    email = _current_user_email()

    lakebase.run_write(
        f"""
        INSERT INTO {WATCHLIST_TABLE_NAME}
            (symbol, email, latest_price, updated_at)
        VALUES
            (%s, %s, %s, now())
        ON CONFLICT (symbol, email)
        DO UPDATE SET
            latest_price = EXCLUDED.latest_price,
            updated_at = EXCLUDED.updated_at
        """,
        (
            symbol,
            email,
            price,
        ),
    )

    return jsonify({
        "symbol": symbol,
        "email": email,
        "latest_price": price,
    })

@app.route("/watchlist/<symbol>", methods=["DELETE"])
def remove_from_watchlist(symbol):
    """
    Remove one stock symbol from the current user's watchlist.
    """
    ensure_watchlist_table()

    symbol = symbol.strip().upper()
    email = _current_user_email()

    if not symbol or not _TICKER_RE.match(symbol):
        return jsonify({
            "error": f"Invalid ticker symbol: {symbol!r}"
        }), 400

    lakebase.run_write(
        f"""
        DELETE FROM {WATCHLIST_TABLE_NAME}
        WHERE symbol = %s
          AND email = %s
        """,
        (symbol, email),
    )

    return jsonify({
        "message": f"{symbol} removed from watchlist",
        "symbol": symbol
    })

def _extract_latest_price(data: dict) -> float | None:
    """
    Extract the price from Massive's previous-day aggregate response.

    Expected response shape:

    {
        "status": "OK",
        "resultsCount": 1,
        "results": [
            {
                "c": 148.845
            }
        ]
    }
    """

    if not isinstance(data, dict):
        return None

    # Massive may return status=OK for successful responses
    if data.get("status") not in (None, "OK"):
        return None

    # Explicit empty result
    if data.get("resultsCount") == 0:
        return None

    results = data.get("results", data)

    # Previous-day endpoint returns results as a list
    if isinstance(results, list):
        results = results[0] if results else None

    if not isinstance(results, dict):
        return None

    # "c" = close price
    # Keep fallbacks in case API shape changes
    for key in (
        "c",
        "p",
        "price",
        "last_price",
        "vw",
    ):
        if key in results and results[key] is not None:
            try:
                return float(results[key])
            except (TypeError, ValueError):
                return None

    return None


def _upsert_batch(items: list[dict]) -> int:
    """
    Upsert Massive API items into Lakebase.
    """
    import json as _json

    count = 0

    with lakebase.get_connection() as conn:

        with conn.cursor() as cur:

            for item in items:

                cur.execute(
                    f"""
                    INSERT INTO {TABLE_NAME}
                        (id, payload, synced_at)
                    VALUES
                        (%s, %s, now())
                    ON CONFLICT (id)
                    DO UPDATE SET
                        payload = EXCLUDED.payload,
                        synced_at = EXCLUDED.synced_at
                    """,
                    (
                        str(item.get("id")),
                        _json.dumps(item),
                    ),
                )

                count += 1

            conn.commit()

    return count


if __name__ == "__main__":

    host = os.getenv(
        "FLASK_RUN_HOST",
        "0.0.0.0"
    )

    port = int(
        os.getenv(
            "FLASK_RUN_PORT",
            8000
        )
    )

    app.run(
        debug=True,
        host=host,
        port=port
    )