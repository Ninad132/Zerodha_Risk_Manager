from kiteconnect import KiteTicker
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import traceback
import json
import get_logger
import datetime as dt
import pytz
import logging
import csv
import subprocess 
import fcntl
import config
import get_kite_client


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ist = pytz.timezone("Asia/Kolkata")

logger = get_logger.get_logger("Risk_Manager")

current_file_path = os.path.dirname(os.path.realpath(__file__))
subscribed_tokens=set()
price_cache = {}
risk_manager_lock = None
last_daily_journal_write = 0
latest_daily_journal_values = {}


kill_switch_path = os.path.join(
        current_file_path,
        "kill_switch.py"
    )


RISK_STATE_FILE = os.path.join(
    current_file_path,
    "risk_state.json"
)

JOURNAL_FILE = os.path.join(
    current_file_path,
    "trade_journal.csv"
)

DAILY_JOURNAL_FILE = os.path.join(
    current_file_path,
    "daily_trade_journal.csv"
)

DAILY_JOURNAL_FIELDS = [
    "trading_date",
    "client_id",
    "first_update",
    "last_update",
    "status",
    "opening_balance",
    "daily_loss_limit",
    "closing_mtm",
    "peak_mtm",
    "lowest_mtm",
    "completed_orders",
    "open_positions",
    "capital_used",
    "kill_switch_triggered",
    "kill_reason",
    "notes"
]
DAILY_JOURNAL_UPDATE_INTERVAL_SECONDS = 60

RISK_MANAGER_LOCK_FILE = os.path.join(
    current_file_path,
    "risk_manager.lock"
)

NO_NEW_TRADES_START = dt.time(10, 45)
NO_NEW_TRADES_END = dt.time(12, 45)
NO_NEW_TRADES_REASON = "No New Trades Between 10:45 AM and 12:45 PM"
CAPITAL_USAGE_REASON = "Capital Usage Limit Exceeded"


class StaleMarketDataError(Exception):
    pass


class RiskCalculationError(Exception):
    pass

def load_risk_state():

    if not os.path.exists(RISK_STATE_FILE):

        return {
            "date": "",
            "lockdown": False,
            "reason": "",
            "peak_mtm": 0,
            "kill_switch_triggered": False
        }

    with open(RISK_STATE_FILE) as f:
        return json.load(f)


def today_ist():
    return dt.datetime.now(ist).strftime(
        "%Y-%m-%d"
    )


def now_ist_string():
    return dt.datetime.now(ist).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def save_risk_state(state):
    temp_file = f"{RISK_STATE_FILE}.tmp"

    with open(temp_file, "w") as f:
        json.dump(
            state,
            f,
            indent=4
        )
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_file, RISK_STATE_FILE)


def log_journal(
    event,
    client_id="",
    mtm=0,
    peak_mtm=0,
    orders=0,
    open_positions=0,
    reason=""
):

    file_exists = os.path.exists(
        JOURNAL_FILE
    )

    with open(
        JOURNAL_FILE,
        "a",
        newline=""
    ) as f:

        writer = csv.writer(f)

        if not file_exists:

            writer.writerow([
                "timestamp",
                "client_id",
                "event",
                "mtm",
                "peak_mtm",
                "orders",
                "open_positions",
                "reason"
            ])

        writer.writerow([
            now_ist_string(),
            client_id,
            event,
            mtm,
            peak_mtm,
            orders,
            open_positions,
            reason
        ])


def _number(value, default=0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def update_daily_journal(
    client_id,
    opening_balance=None,
    daily_loss_limit=None,
    mtm=None,
    completed_orders=None,
    open_positions=None,
    capital_used=None,
    status=None,
    kill_switch_triggered=None,
    kill_reason=None,
    notes=None
):
    """Upsert one restart-safe summary row per client and trading day."""
    global last_daily_journal_write
    global latest_daily_journal_values

    supplied_values = {
        "opening_balance": opening_balance,
        "daily_loss_limit": daily_loss_limit,
        "mtm": mtm,
        "completed_orders": completed_orders,
        "open_positions": open_positions,
        "capital_used": capital_used,
        "status": status,
        "kill_switch_triggered": kill_switch_triggered,
        "kill_reason": kill_reason,
        "notes": notes
    }
    latest_daily_journal_values.update(
        {
            field: value
            for field, value in supplied_values.items()
            if value is not None
        }
    )

    terminal_statuses = {"COMPLETED", "LOCKDOWN", "STOPPED"}
    now_monotonic = time.monotonic()
    if (
        last_daily_journal_write
        and status not in terminal_statuses
        and now_monotonic - last_daily_journal_write
        < DAILY_JOURNAL_UPDATE_INTERVAL_SECONDS
    ):
        return

    opening_balance = latest_daily_journal_values.get("opening_balance")
    daily_loss_limit = latest_daily_journal_values.get("daily_loss_limit")
    mtm = latest_daily_journal_values.get("mtm")
    completed_orders = latest_daily_journal_values.get("completed_orders")
    open_positions = latest_daily_journal_values.get("open_positions")
    capital_used = latest_daily_journal_values.get("capital_used")
    status = latest_daily_journal_values.get("status")
    kill_switch_triggered = latest_daily_journal_values.get(
        "kill_switch_triggered"
    )
    kill_reason = latest_daily_journal_values.get("kill_reason")
    notes = latest_daily_journal_values.get("notes")

    rows = []
    if os.path.exists(DAILY_JOURNAL_FILE):
        with open(DAILY_JOURNAL_FILE, newline="") as f:
            rows = list(csv.DictReader(f))

    trading_date = today_ist()
    row = next(
        (
            item for item in rows
            if item.get("trading_date") == trading_date
            and item.get("client_id") == client_id
        ),
        None
    )

    if row is None:
        row = {field: "" for field in DAILY_JOURNAL_FIELDS}
        row.update({
            "trading_date": trading_date,
            "client_id": client_id,
            "first_update": now_ist_string(),
            "status": "RUNNING",
            "kill_switch_triggered": "False"
        })
        rows.append(row)

    row["last_update"] = now_ist_string()

    updates = {
        "opening_balance": opening_balance,
        "daily_loss_limit": daily_loss_limit,
        "closing_mtm": mtm,
        "completed_orders": completed_orders,
        "open_positions": open_positions,
        "capital_used": capital_used,
        "status": status,
        "kill_reason": kill_reason,
        "notes": notes
    }
    for field, value in updates.items():
        if value is not None:
            row[field] = value

    if mtm is not None:
        if row["peak_mtm"] == "":
            row["peak_mtm"] = mtm
            row["lowest_mtm"] = mtm
        else:
            row["peak_mtm"] = max(_number(row["peak_mtm"]), mtm)
            row["lowest_mtm"] = min(_number(row["lowest_mtm"]), mtm)

    if kill_switch_triggered is not None:
        row["kill_switch_triggered"] = str(bool(kill_switch_triggered))

    temp_file = f"{DAILY_JOURNAL_FILE}.tmp"
    with open(temp_file, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=DAILY_JOURNAL_FIELDS)
        writer.writeheader()
        writer.writerows(
            {field: item.get(field, "") for field in DAILY_JOURNAL_FIELDS}
            for item in rows
        )
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_file, DAILY_JOURNAL_FILE)
    last_daily_journal_write = now_monotonic

def activate_lockdown(reason, client_id=""):

    state = load_risk_state()

    state["lockdown"] = True
    state["reason"] = reason
    state["date"] = today_ist()

    save_risk_state(state)

    log_journal(
        event="LOCKDOWN_ACTIVATE",
        client_id=client_id,
        reason=reason
    )

    update_daily_journal(
        client_id=client_id,
        status="LOCKDOWN",
        kill_switch_triggered=True,
        kill_reason=reason
    )

    enforce_lockdown_clear(client_id)


def monitor_lockdown(positions):

    try:
        # positions = kite.positions()

        active_positions = [

            p

            for p in positions

            if p["quantity"] != 0
        ]

        if active_positions:

            log_journal(
                event="POSITION_DETECTED_DURING_LOCKDOWN"
            )
            enforce_lockdown_clear()

    except Exception:

        logger.error(
            traceback.format_exc()
        )


def get_opening_balance(kite):

    margins = kite.margins()

    # Test
    # margins = float(input("Enter Opening Balance and press Enter: "))
    # print(margins["equity"]["net"])

    return margins["equity"]["net"]
    # return margins 

def update_peak_mtm(mtm):

    state = load_risk_state()
    peak = state.get("peak_mtm", 0)

    if mtm > peak:

        state["peak_mtm"] = mtm

        save_risk_state(state)

        logger.info(
            f"New Peak MTM={mtm}"
        )


def check_profit_protection(mtm, opening_balance, client_id=""):
    state = load_risk_state()

    config.PEAK_MTM = state.get(
    "peak_mtm",
    0
    )
    config.PROFIT_LOCK_TRIGGER = round(opening_balance * 0.07)

    # Protection not active yet
    if config.PEAK_MTM < config.PROFIT_LOCK_TRIGGER:
        return False

    protected_profit = (
        config.PEAK_MTM *
        config.PROFIT_RETENTION_PERCENT
    )

    logger.info(
        f"Peak MTM={config.PEAK_MTM}, "
        f"Protected Profit={protected_profit}, "
        f"Current MTM={mtm}"
    )

    if mtm <= protected_profit:

        logger.warning(
            f"Profit Protection Triggered. "
            f"Peak={config.PEAK_MTM}, "
            f"Current={mtm}"
        )

        log_journal(
            event="PROFIT_PROTECTION_TRIGGERED",
            client_id=client_id,
            mtm=mtm,
            peak_mtm=config.PEAK_MTM,
            reason="Profit protection triggered"
        )

        return True

    return False

def on_ticks(ws, ticks):
    now = time.monotonic()
    for tick in ticks:
        token = tick["instrument_token"]
        price = tick["last_price"]
        config.live_ltp_dict[token]=price
        price_cache[token] = {
            "price": price,
            "received_at": now,
            "source": "websocket"
        }
        # print(f"Received {len(ticks)} ticks")
        # token = tick['instrument_token']
        # print(tick["instrument_token"])
        # # print(config.open_positions.keys())
        # if token in config.open_positions:
        #     # Update the Last Traded Price (LTP) for the instrument
        #     print(f"Received tick for {config.open_positions[token]}: LTP={tick['last_price']}")
        #     ltp = tick['last_price']
        #     config.open_positions[token]['last_price'] = ltp

def on_connect(ws, response):
    logger.info("Risk_Manager WebSocket Connected")
    try:

        positions = get_open_positions()

        tokens = [
        p["instrument_token"]
        for p in positions
        ]

        if tokens:

            ws.subscribe(tokens)

            ws.set_mode(
            ws.MODE_QUOTE,
            tokens
            )
            subscribed_tokens.update(tokens)
            logger.info(
            f"Subscribed {len(tokens)} tokens"
            )
    except Exception as e:
        logging.error(f"Error subscribing: {e}")


def on_close(ws, code, reason):
    logger.warning(
        f"Risk_Manager WebSocket closed. code={code}, reason={reason}"
    )
    log_journal(
        event="WEBSOCKET_CLOSED",
        reason=f"code={code}, reason={reason}"
    )


def on_error(ws, code, reason):
    logger.error(
        f"Risk_Manager WebSocket error. code={code}, reason={reason}"
    )
    log_journal(
        event="WEBSOCKET_ERROR",
        reason=f"code={code}, reason={reason}"
    )


def get_position_instrument(position):
    return f"{position['exchange']}:{position['tradingsymbol']}"


def get_cached_price(token, now):
    cached = price_cache.get(token)

    if not cached:
        return None

    age = now - cached["received_at"]
    if age > config.PRICE_MAX_AGE_SECONDS:
        return None

    return cached["price"]


def get_positions_needing_price_refresh(positions):
    now = time.monotonic()
    stale_positions = []

    for position in positions:
        if position["quantity"] == 0:
            continue

        token = position["instrument_token"]
        if get_cached_price(token, now) is None:
            stale_positions.append(position)

    return stale_positions


def refresh_prices_from_rest(positions):
    if not positions:
        return

    instruments_by_token = {
        position["instrument_token"]: get_position_instrument(position)
        for position in positions
    }

    try:
        quotes = kite.ltp(list(instruments_by_token.values()))
    except Exception:
        logger.exception(
            "Failed to refresh stale market data through REST LTP."
        )
        return

    now = time.monotonic()

    for token, instrument in instruments_by_token.items():
        quote = quotes.get(instrument)

        if not quote or quote.get("last_price") is None:
            logger.warning(
                f"REST LTP missing for {instrument}"
            )
            continue

        price = quote["last_price"]
        config.live_ltp_dict[token] = price
        price_cache[token] = {
            "price": price,
            "received_at": now,
            "source": "rest"
        }


def ensure_fresh_prices(positions):
    stale_positions = get_positions_needing_price_refresh(positions)

    if stale_positions:
        logger.warning(
            "Refreshing stale/missing prices for "
            f"{[get_position_instrument(position) for position in stale_positions]}"
        )
        refresh_prices_from_rest(stale_positions)

    still_stale_positions = get_positions_needing_price_refresh(positions)

    if still_stale_positions:
        instruments = [
            get_position_instrument(position)
            for position in still_stale_positions
        ]
        raise StaleMarketDataError(
            f"Fresh market data unavailable for {instruments}"
        )


def get_fresh_price(position):
    token = position["instrument_token"]
    price = get_cached_price(token, time.monotonic())

    if price is None:
        raise StaleMarketDataError(
            f"Fresh market data unavailable for {get_position_instrument(position)}"
        )

    return price

def start_websocket():

    client_doc = get_kite_client.get_client_doc_from_json()

    api_key = client_doc["api_key"]
    access_token = client_doc["access_token"]

    config.kws = KiteTicker(
        api_key,
        access_token
    )

    config.kws.on_ticks = on_ticks
    config.kws.on_connect = on_connect
    config.kws.on_close = on_close
    config.kws.on_error = on_error

def get_all_positions():
    positions = kite.positions()
    return positions["net"]

def get_open_positions():
    positions = kite.positions()
    return [p for p in positions["net"] if p["quantity"] != 0]

def sync_subscriptions():
    global subscribed_tokens
    positions = get_open_positions()
    current_tokens = {
        p["instrument_token"]
        for p in positions
    }

    new_tokens = (
        current_tokens -
        subscribed_tokens
    )

    if new_tokens:

        config.kws.subscribe(
            list(new_tokens)
        )

        config.kws.set_mode(
            config.kws.MODE_QUOTE,
            list(new_tokens)
        )

        subscribed_tokens.update(
            new_tokens
        )

        logger.info(
            f"Subscribed new tokens "
            f"{new_tokens}"
        )

    stale_tokens = (
        subscribed_tokens -
        current_tokens
    )

    if stale_tokens:
        try:
            config.kws.unsubscribe(
                list(stale_tokens)
            )
        except Exception:
            logger.exception(
                f"Failed to unsubscribe stale tokens {stale_tokens}"
            )

        subscribed_tokens -= stale_tokens
        logger.info(
            f"Unsubscribed stale tokens "
            f"{stale_tokens}"
        )

def calculate_mtm(positions):
    if config.kws and not config.kws.is_connected():
        logger.warning(
            "WebSocket not connected. MTM will require fresh REST LTP fallback."
        )

    ensure_fresh_prices(positions)

    total_mtm=0.0

    for pos in positions:
        current_price = 0

        if pos["quantity"] != 0:
            current_price = get_fresh_price(pos)

        mtm = (pos['sell_value'] - pos['buy_value']) + \
                (pos['quantity'] * current_price * pos['multiplier'])
        total_mtm += mtm
    return total_mtm


def check_kill_condition(kite,positions):
    try:
        orders = kite.orders()
        total_mtm = calculate_mtm(positions)
        # Total number of Orders. This will limit the number of trades for the day .
        completed_orders_count = sum(
            1 for order in orders if order["status"] == "COMPLETE"
        )
        open_positions = sum(
            1 for position in positions if position["quantity"] != 0
        )
        # print(
        #     "MTM=",
        #     total_mtm,
        #     "order count = ",
        #     completed_orders_count,
        #     "Open orders=",
        #     open_positions,
        # )

        # #Tests
        # MTM = float(input("enter MTM and press Enter: "))
        # completed_orders_count = int(input("Enter number of completed orders and press Enter: "))
        # open_positions = int(input("Enter number of open positions and press Enter: "))


        return total_mtm, completed_orders_count, open_positions
    except StaleMarketDataError:
        raise
    except Exception as e:
        raise RiskCalculationError(
            f"Error calculating MTM: {traceback.format_exc()}"
        )


def cancel_all_orders(kite):

    orders = kite.orders()
    cancel_orders(kite, orders)


def cancel_orders(kite, orders):

    for order in orders:

        if order["status"] in [
            "COMPLETE",
            "CANCELLED",
            "REJECTED"
        ]:
            continue

        try:
            logger.info(
                f"Cancelling {order['order_id']} "
                f"status={order['status']}"
            )

            kite.cancel_order(
                variety=order["variety"],
                order_id=order["order_id"]
            )

        except Exception:
            logger.exception(
                f"Cancel failed "
                f"{order['order_id']}"
            )


def exit_positions(kite, positions_to_exit):
    try:
        open_positions = [
            position
            for position in positions_to_exit
            if position["quantity"] != 0
        ]
        for position in open_positions:
            transaction_type = "SELL" if position["quantity"] > 0 else "BUY"
            logger.info(f"Exiting position: {position}")
            kite.place_order(
                variety="regular",
                exchange=position["exchange"],
                tradingsymbol=position["tradingsymbol"],
                transaction_type=transaction_type,
                quantity=abs(position["quantity"]),
                order_type="MARKET",
                product=position["product"],
                market_protection=-1,
            )
    except Exception as e:
        logger.error(f"Error exiting positions: {traceback.format_exc()}")


def exit_all_positions(kite):
    # print("Exiting all positions...")
    positions = kite.positions()
    exit_positions(kite, positions["net"])


def get_active_orders(kite):
    inactive_statuses = {
        "COMPLETE",
        "CANCELLED",
        "REJECTED"
    }

    return [
        order
        for order in kite.orders()
        if order["status"] not in inactive_statuses
    ]


def is_no_new_trades_window(now=None):
    if now is None:
        now = dt.datetime.now(ist)

    current_time = now.time()
    return (
        NO_NEW_TRADES_START <= current_time < NO_NEW_TRADES_END
    )


def parse_order_timestamp(timestamp):
    if timestamp is None:
        return None

    if isinstance(timestamp, dt.datetime):
        if timestamp.tzinfo is None:
            return ist.localize(timestamp)
        return timestamp.astimezone(ist)

    if isinstance(timestamp, str):
        for timestamp_format in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%f%z"
        ):
            try:
                parsed = dt.datetime.strptime(
                    timestamp,
                    timestamp_format
                )
                if parsed.tzinfo is None:
                    return ist.localize(parsed)
                return parsed.astimezone(ist)
            except ValueError:
                continue

    return None


def get_order_timestamps(order):
    timestamps = []

    for field in (
        "exchange_timestamp",
        "order_timestamp",
        "exchange_update_timestamp",
        "created_at"
    ):
        timestamp = parse_order_timestamp(order.get(field))
        if timestamp is not None:
            timestamps.append(timestamp)

    return timestamps


def is_order_from_no_new_trades_window(order):
    if order.get("status") != "COMPLETE":
        return False

    timestamps = get_order_timestamps(order)
    if not timestamps:
        logger.warning(
            f"Cannot determine timestamp for order {order.get('order_id')}"
        )
        return False

    return any(
        is_no_new_trades_window(timestamp)
        for timestamp in timestamps
    )


def get_position_key(item):
    return (
        item.get("exchange"),
        item.get("tradingsymbol"),
        item.get("product")
    )


def is_entry_order_for_position(order, position):
    if get_position_key(order) != get_position_key(position):
        return False

    if position["quantity"] > 0:
        return order.get("transaction_type") == "BUY"

    if position["quantity"] < 0:
        return order.get("transaction_type") == "SELL"

    return False


def is_active_order(order):
    return order["status"] not in {
        "COMPLETE",
        "CANCELLED",
        "REJECTED"
    }


def is_active_new_trade_order(order, positions_by_key):
    if not is_active_order(order):
        return False

    position = positions_by_key.get(get_position_key(order))

    if not position or position["quantity"] == 0:
        return True

    return is_entry_order_for_position(order, position)


def get_positions_opened_in_no_new_trades_window(orders, positions):
    blocked_entry_orders = [
        order
        for order in orders
        if is_order_from_no_new_trades_window(order)
    ]

    return [
        position
        for position in positions
        if position["quantity"] != 0
        and any(
            is_entry_order_for_position(order, position)
            for order in blocked_entry_orders
        )
    ]


def enforce_no_new_trades_window(positions, client_id=""):
    current_window = is_no_new_trades_window()
    orders = kite.orders()
    positions_by_key = {
        get_position_key(position): position
        for position in positions
        if position["quantity"] != 0
    }
    active_new_trade_orders = [
        order
        for order in orders
        if current_window
        and is_active_new_trade_order(order, positions_by_key)
    ]
    positions_to_exit = get_positions_opened_in_no_new_trades_window(
        orders,
        positions
    )

    if not active_new_trade_orders and not positions_to_exit:
        return

    logger.warning(
        f"{NO_NEW_TRADES_REASON}: "
        f"active_new_trade_orders={len(active_new_trade_orders)}, "
        f"positions_to_exit={len(positions_to_exit)}"
    )
    log_journal(
        event="RULE_TRIGGER",
        client_id=client_id,
        open_positions=len(positions_to_exit),
        reason=NO_NEW_TRADES_REASON
    )

    cancel_orders(kite, active_new_trade_orders)
    exit_positions(kite, positions_to_exit)


def get_position_capital_used(position):
    quantity = abs(position.get("quantity", 0))
    price = position.get("average_price") or 0
    multiplier = position.get("multiplier") or 1

    return quantity * price * multiplier


def get_order_price_for_capital_check(order):
    for field in (
        "average_price",
        "price",
        "trigger_price"
    ):
        price = order.get(field)
        if price:
            return price

    return None


def get_order_capital_required(order):
    price = get_order_price_for_capital_check(order)
    if price is None:
        return None

    quantity = (
        order.get("pending_quantity")
        or order.get("quantity")
        or 0
    )
    return abs(quantity) * price


def get_open_capital_used(positions):
    return sum(
        get_position_capital_used(position)
        for position in positions
        if position["quantity"] != 0
    )


def get_capital_limit(opening_balance):
    return opening_balance * config.MAX_CAPITAL_USAGE_FRACTION


def get_orders_breaching_capital_limit(
    orders,
    positions,
    capital_limit
):
    projected_capital_used = get_open_capital_used(positions)
    positions_by_key = {
        get_position_key(position): position
        for position in positions
        if position["quantity"] != 0
    }
    breaching_orders = []

    for order in orders:
        if not is_active_new_trade_order(order, positions_by_key):
            continue

        order_capital_required = get_order_capital_required(order)
        if order_capital_required is None:
            breaching_orders.append(order)
            continue

        if projected_capital_used + order_capital_required > capital_limit:
            breaching_orders.append(order)
            continue

        projected_capital_used += order_capital_required

    return breaching_orders


def enforce_capital_usage_limit(
    positions,
    opening_balance,
    client_id=""
):
    capital_limit = get_capital_limit(opening_balance)
    open_capital_used = get_open_capital_used(positions)
    orders = kite.orders()
    breaching_orders = get_orders_breaching_capital_limit(
        orders,
        positions,
        capital_limit
    )
    positions_to_exit = [
        position
        for position in positions
        if position["quantity"] != 0
    ] if open_capital_used > capital_limit else []

    if not breaching_orders and not positions_to_exit:
        return

    logger.warning(
        f"{CAPITAL_USAGE_REASON}: "
        f"open_capital_used={open_capital_used}, "
        f"capital_limit={capital_limit}, "
        f"breaching_orders={len(breaching_orders)}, "
        f"positions_to_exit={len(positions_to_exit)}"
    )
    log_journal(
        event="RULE_TRIGGER",
        client_id=client_id,
        open_positions=len(positions_to_exit),
        reason=CAPITAL_USAGE_REASON
    )

    cancel_orders(kite, breaching_orders)
    exit_positions(kite, positions_to_exit)


def enforce_lockdown_clear(client_id=""):
    for attempt in range(1, config.LOCKDOWN_VERIFY_ATTEMPTS + 1):
        try:
            active_orders = get_active_orders(kite)
            positions = kite.positions()["net"]
            open_positions = [
                position
                for position in positions
                if position["quantity"] != 0
            ]

            if not active_orders and not open_positions:
                logger.info(
                    "Lockdown verified: no active orders or open positions."
                )
                log_journal(
                    event="LOCKDOWN_VERIFIED",
                    client_id=client_id,
                    reason="No active orders or open positions"
                )
                return True

            logger.warning(
                f"Lockdown verification attempt {attempt}: "
                f"active_orders={len(active_orders)}, "
                f"open_positions={len(open_positions)}"
            )

            cancel_all_orders(kite)
            exit_all_positions(kite)

        except Exception:
            logger.exception(
                f"Lockdown verification attempt {attempt} failed"
            )

        time.sleep(config.LOCKDOWN_VERIFY_SLEEP_SECONDS)

    logger.error(
        "Lockdown verification failed after all attempts."
    )
    log_journal(
        event="LOCKDOWN_VERIFY_FAILED",
        client_id=client_id,
        reason="Active orders or open positions may remain after lockdown"
    )
    return False


def is_market_open():
    now = dt.datetime.now(ist)
    return (
        now.weekday() < 5
        and (now.hour > 9 or (now.hour == 9 and now.minute >= 00))
        and (now.hour < 15 or (now.hour == 15 and now.minute < 30))
    )

def is_trading_allowed():

    now = dt.datetime.now(ist)

    if now.hour > 14:
        return False

    if now.hour == 14 and now.minute >= 15:
        return False

    return True


def ensure_daily_threshold(kite,opening_balance):

    daily_loss_limit = round(
        opening_balance * 0.13,
    )

    logger.info(
        f"Opening Balance: {opening_balance}"
    )
    logger.info(
        f"Daily Loss Limit: {daily_loss_limit}"
    )


    return daily_loss_limit

def run_engine():
    client_id = get_kite_client.get_single_client_id()

    logger.info(
        f"Starting Restart-Safe Trading Risk Engine for {client_id}"
    )
    global kite

    kite = get_kite_client.get_kite_client()
    start_websocket()
    config.kws.connect(threaded=True)
    time.sleep(2)
    
    SL_manager_path = os.path.join(
        current_file_path,
        "SL_Manager.py"
    )

    log_file = open(os.path.join(current_file_path, "SL_manager.log"), "a")

    process = subprocess.Popen(
        [sys.executable, SL_manager_path],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True
    )

    try:
        opening_balance = get_opening_balance(kite)
        loss_threshold = ensure_daily_threshold(kite, opening_balance)
        update_daily_journal(
            client_id=client_id,
            opening_balance=opening_balance,
            daily_loss_limit=loss_threshold,
            status="RUNNING",
            notes="No completed orders yet"
        )
        while True:
            positions = get_all_positions()
            sync_subscriptions()

            #Close Risk Manager at 18:00 PM and start fresh the next day. This is to avoid any issues with the API or the system. We can also do this to avoid any issues with the market data or the orders. This will also help us to start fresh the next day and avoid any issues with the previous day's data.
            now = dt.datetime.now(ist)
            print("Current time: ", now.hour, now.minute)
            if now.hour >= 18:
                logger.info("Day Complete. No More Trading Allowed. Shutting down Risk Manager for the day. ")
                update_daily_journal(
                    client_id=client_id,
                    status="COMPLETED"
                )
                sys.exit()

            today = today_ist()
            state = load_risk_state()
            if state["date"] != today:
                state = {
                    "date": today,
                    "lockdown": False,
                    "reason": "",
                    "peak_mtm": 0,
                    "kill_switch_triggered": False
                }
                save_risk_state(state)

            if state["lockdown"]:
                monitor_lockdown(positions)
                if not state.get("kill_switch_triggered", False):
                    log_file = open(os.path.join(current_file_path, "risk_manager.log"), "a")
                    subprocess.Popen(
                        [sys.executable, kill_switch_path], stdout=log_file, stderr=log_file, start_new_session=True)
                    state["kill_switch_triggered"] = True
                    save_risk_state(state)
                    sys.exit()
                else:
                    logger.info("Day Complete. No More Trading Allowed. Shutting down Risk Manager for the day. ")
                    sys.exit()

            start_time = time.time()
            now = dt.datetime.now(ist)

            if not is_market_open():
                logger.info("Market closed. Sleeping 5 minutes...")
                time.sleep(300)
                continue

            try:
                enforce_no_new_trades_window(positions, client_id)
                enforce_capital_usage_limit(
                    positions,
                    opening_balance,
                    client_id
                )

                MTM, order_len, open_orders = check_kill_condition(kite,positions)
                update_peak_mtm(MTM)

                update_daily_journal(
                    client_id=client_id,
                    opening_balance=opening_balance,
                    daily_loss_limit=loss_threshold,
                    mtm=MTM,
                    completed_orders=order_len,
                    open_positions=open_orders,
                    capital_used=get_open_capital_used(positions),
                    status="RUNNING",
                    notes=(
                        "No trades taken"
                        if order_len == 0
                        else "Trading activity recorded"
                    )
                )

                logger.info(
                    f"{client_id} MTM: {MTM} | Threshold: {loss_threshold}"
                )

                # Rule 1: Profit Protection
                if check_profit_protection(MTM, opening_balance, client_id):
                    log_journal(event="RULE_TRIGGER",client_id=client_id,mtm=MTM,peak_mtm=config.PEAK_MTM,orders=order_len,open_positions=open_orders,reason="Profit Protection")
                    activate_lockdown("Profit Protection", client_id)

                # Rule 2: Open Position Limit
                if open_orders > config.MAX_OPEN_POSITIONS:
                    log_journal(event="RULE_TRIGGER",client_id=client_id,mtm=MTM,peak_mtm=config.PEAK_MTM,orders=order_len,open_positions=open_orders,reason="Max Open Positions Limit Exceeded")
                    activate_lockdown("Max Open Positions Limit Exceeded", client_id)

                # Rule 3: Daily Loss Limit or max orders limit.
                if MTM <= -loss_threshold or order_len >= 12:
                    log_journal(event="RULE_TRIGGER",client_id=client_id,mtm=MTM,peak_mtm=config.PEAK_MTM,orders=order_len,open_positions=open_orders,reason="Daily Loss Limit Exceeded or Max Orders Reached")
                    activate_lockdown("Daily Loss Limit Exceeded or Max Orders Reached", client_id)


                # Rule 4: profit = 3X loss.
                if MTM > (loss_threshold * 3) and open_orders == 0:
                    log_journal(event="RULE_TRIGGER",client_id=client_id,mtm=MTM,peak_mtm=config.PEAK_MTM,orders=order_len,open_positions=open_orders,reason="3R Profit Protection")
                    activate_lockdown("3R Profit Protection", client_id)

                # Rule 5: No trading after 2:15 PM if flat.
                if not is_trading_allowed():
                    if open_orders == 0:
                        log_journal(event="RULE_TRIGGER",client_id=client_id,mtm=MTM,peak_mtm=config.PEAK_MTM,orders=order_len,open_positions=open_orders,reason="No Trading After 2:15 PM") 
                        activate_lockdown("No Trading After 2:15 PM", client_id)


            except StaleMarketDataError:
                logger.error(
                    f"Stale market data. Skipping MTM checks without cancelling orders: "
                    f"{traceback.format_exc()}"
                )
                log_journal(
                    event="STALE_MARKET_DATA",
                    client_id=client_id,
                    reason="Fresh LTP unavailable; skipped MTM cycle without cancelling orders"
                )
            except RiskCalculationError:
                logger.error(
                    f"Risk calculation failed for {client_id}:\n{traceback.format_exc()}"
                )
                log_journal(
                    event="RISK_CALCULATION_FAILED",
                    client_id=client_id,
                    reason="Risk calculation failed; activating lockdown"
                )
                activate_lockdown("Risk Calculation Failed", client_id)
                sys.exit()
            except Exception:
                logger.error(
                    f"Error processing {client_id}:\n{traceback.format_exc()}"
                )
                log_journal(
                    event="RISK_ENGINE_ERROR",
                    client_id=client_id,
                    reason="Unexpected risk engine error; activating lockdown"
                )
                activate_lockdown("Risk Engine Error", client_id)
                sys.exit()
            # print("Sleeping for {} seconds...".format(config.CHECK_INTERVAL), "\n\n\n\n\n")
            # Maintain fixed interval
            elapsed = time.time() - start_time
            sleep_time = config.CHECK_INTERVAL - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Engine stopped manually.")
        update_daily_journal(
            client_id=client_id,
            status="STOPPED",
            notes="Risk manager stopped manually"
        )
        sys.exit()


def acquire_risk_manager_lock():
    global risk_manager_lock

    risk_manager_lock = open(RISK_MANAGER_LOCK_FILE, "w")

    try:
        fcntl.flock(
            risk_manager_lock,
            fcntl.LOCK_EX | fcntl.LOCK_NB
        )
    except BlockingIOError:
        logger.error(
            "Another Risk Manager process is already running."
        )
        sys.exit(1)


def main():
    if len(sys.argv) > 1:
        logger.error(
            "Risk Manager runs in single-client mode and does not accept a client id argument."
        )
        sys.exit(1)

    acquire_risk_manager_lock()
    run_engine()


if __name__ == "__main__":
    main()
    
