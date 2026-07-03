import config
import time
import os
import json 
import get_kite_client
import get_logger
from kiteconnect import KiteTicker
import datetime as dt
import pytz
import sys

ist = pytz.timezone("Asia/Kolkata")
current_file_path = os.path.dirname(os.path.realpath(__file__))
logger = get_logger.get_logger("SL_Manager")
subscribed_tokens=set()
price_cache = {}
ACTIVE_SL_ORDER_STATUSES = {
    "OPEN",
    "OPEN PENDING",
    "TRIGGER PENDING",
    "VALIDATION PENDING",
}

RISK_STATE_FILE = os.path.join(
    current_file_path,
    "risk_state.json"
)


def load_risk_state():

    if not os.path.exists(RISK_STATE_FILE):

        return {
            "date": "",
            "lockdown": False,
            "reason": "",
            "peak_mtm": 0
        }

    with open(RISK_STATE_FILE) as f:
        return json.load(f)
    
def is_lockdown_activated():
    state = load_risk_state()
    return state.get("lockdown",False)

def on_ticks(ws, ticks):
    now = time.monotonic()

    for tick in ticks:
        token = tick["instrument_token"]
        price = tick["last_price"]

        config.live_ltp_dict[
            token
        ] = price

        price_cache[token] = {
            "price": price,
            "received_at": now,
            "source": "websocket"
        }


def on_connect(ws, response):

    logger.info(
        "SL Manager WebSocket Connected"
    )

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


def on_close(ws, code, reason):
    logger.warning(
        f"SL Manager WebSocket closed. code={code}, reason={reason}"
    )


def on_error(ws, code, reason):
    logger.error(
        f"SL Manager WebSocket error. code={code}, reason={reason}"
    )

def get_open_positions():
    positions = kite.positions()
    return [p for p in positions["net"] if p["quantity"] != 0]


def get_position_side(position):
    return "LONG" if position["quantity"] > 0 else "SHORT"


def get_sl_transaction_type(position):
    return "SELL" if get_position_side(position) == "LONG" else "BUY"


def get_position_instrument(position):
    return f"{position['exchange']}:{position['tradingsymbol']}"


def get_cached_price(token, now):
    cached = price_cache.get(token)

    if not cached:
        return None

    if now - cached["received_at"] > config.PRICE_MAX_AGE_SECONDS:
        return None

    return cached["price"]


def refresh_price_from_rest(position):
    token = position["instrument_token"]
    instrument = get_position_instrument(position)

    try:
        quotes = kite.ltp([instrument])
    except Exception:
        logger.exception(
            f"Failed REST LTP refresh for {instrument}"
        )
        return None

    quote = quotes.get(instrument)
    if not quote or quote.get("last_price") is None:
        logger.warning(
            f"REST LTP missing for {instrument}"
        )
        return None

    price = quote["last_price"]
    config.live_ltp_dict[token] = price
    price_cache[token] = {
        "price": price,
        "received_at": time.monotonic(),
        "source": "rest"
    }

    return price


def get_ltp(position):
    token = position["instrument_token"]
    price = get_cached_price(token, time.monotonic())

    if price is not None:
        return price

    logger.warning(
        f"Missing/stale LTP for {get_position_instrument(position)}. "
        "Trying REST LTP fallback."
    )

    return refresh_price_from_rest(position)


def calculate_initial_trigger(position):
    entry_price = position["average_price"]

    if get_position_side(position) == "LONG":
        return round(entry_price * (1 - config.SL_PERCENT / 100), 1)

    return round(entry_price * (1 + config.SL_PERCENT / 100), 1)


def is_valid_trigger(position, trigger_price, ltp):
    if get_position_side(position) == "LONG":
        return trigger_price < ltp

    return trigger_price > ltp

def sync_subscriptions(positions):
    global subscribed_tokens

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

def place_SL_order(position):
    token = position["instrument_token"]
    trigger_price = calculate_initial_trigger(position)
    ltp = get_ltp(position)

    if ltp is None or not is_valid_trigger(position, trigger_price, ltp):

        logger.warning(f"""Cannot place SL
        Token   : {token}
        Trigger : {trigger_price}
        LTP     : {ltp}
        """
        )
        return None

    order_id = kite.place_order (
        variety="regular",
        exchange=position["exchange"],
        tradingsymbol=position["tradingsymbol"],
        transaction_type=get_sl_transaction_type(position),
        quantity=abs(position["quantity"]),
        product=position["product"],
        order_type="SL-M",
        trigger_price=trigger_price,
        market_protection=-1)
    
    logger.info(
        f"SL placed for {position['tradingsymbol']} "
        f"at {trigger_price}"
    )
    return order_id, trigger_price


def is_active_sl_order(order):
    return (
        order.get("order_type") == "SL-M"
        and order.get("status") in ACTIVE_SL_ORDER_STATUSES
    )


def order_matches_position(order, position):
    return (
        order.get("exchange") == position["exchange"]
        and order.get("tradingsymbol") == position["tradingsymbol"]
        and order.get("product") == position["product"]
        and order.get("transaction_type") == get_sl_transaction_type(position)
    )


def get_order_created_at(order):
    return (
        order.get("order_timestamp")
        or order.get("exchange_timestamp")
        or order.get("exchange_update_timestamp")
        or ""
    )


def track_position_from_order(position, order):
    token = position["instrument_token"]
    current_price = get_ltp(position)
    entry_price = position["average_price"]

    if get_position_side(position) == "LONG":
        best_price = max(entry_price, current_price or entry_price)
    else:
        best_price = min(entry_price, current_price or entry_price)

    config.tracked_positions[token] = {
        "quantity": position["quantity"],
        "entry_price": entry_price,
        "sl_order_id": order["order_id"],
        "side": get_position_side(position),
        "best_price": best_price,
        "current_trigger": float(order["trigger_price"])
    }


def reconcile_existing_sl_orders(positions):
    orders = [
        order
        for order in kite.orders()
        if is_active_sl_order(order)
    ]

    for position in positions:
        matching_orders = [
            order
            for order in orders
            if order_matches_position(order, position)
        ]

        if not matching_orders:
            continue

        matching_orders.sort(key=get_order_created_at, reverse=True)

        exact_orders = [
            order
            for order in matching_orders
            if order.get("quantity") == abs(position["quantity"])
        ]

        if not exact_orders:
            logger.warning(
                f"Found active SL orders for {position['tradingsymbol']} "
                "but none match the current position quantity. "
                "Cancelling them before placing a fresh SL."
            )
            for stale_order in matching_orders:
                cancel_order(stale_order["order_id"])
            continue

        order_to_track = exact_orders[0]
        track_position_from_order(position, order_to_track)

        for duplicate_order in matching_orders:
            if duplicate_order["order_id"] != order_to_track["order_id"]:
                logger.warning(
                    f"Cancelling duplicate SL order "
                    f"{duplicate_order['order_id']} for "
                    f"{position['tradingsymbol']}"
                )
                cancel_order(duplicate_order["order_id"])

        logger.info(
            f"Reconciled existing SL order "
            f"{order_to_track['order_id']} for "
            f"{position['tradingsymbol']}"
        )

def trail_stop_loss(pos):

    token = pos["instrument_token"]

    tracked = config.tracked_positions[token]

    current_price = get_ltp(pos)

    if current_price is None:
        return

    if tracked["side"] == "LONG":
        is_new_best_price = current_price > tracked["best_price"]
        new_trigger = round(
            current_price *
            (1 - config.TRAILING_SL_PERCENT / 100),
            1
        )
        should_modify = new_trigger > tracked["current_trigger"]
    else:
        is_new_best_price = current_price < tracked["best_price"]
        new_trigger = round(
            current_price *
            (1 + config.TRAILING_SL_PERCENT / 100),
            1
        )
        should_modify = new_trigger < tracked["current_trigger"]

    if not is_new_best_price:
        return

    tracked["best_price"] = current_price

    if should_modify:

        logger.info(
            f"{pos['tradingsymbol']} | "
            f"Best={current_price} | "
            f"Old SL={tracked['current_trigger']} | "
            f"New SL={new_trigger}"
        )

        try:

            kite.modify_order(
                variety="regular",
                order_id=tracked["sl_order_id"],
                trigger_price=new_trigger
            )


            tracked["current_trigger"] = new_trigger
            logger.info(
                f"Modified SL to {new_trigger}"
            )

        except Exception:

            logger.exception(
                f"Failed trailing SL for "
                f"{pos['tradingsymbol']}"
            )

def monitor_new_positions(positions):
    for pos in positions:

        token = pos["instrument_token"]

        if token not in config.tracked_positions:

            sl_order = place_SL_order(pos)
            if sl_order:
                sl_order_id, trigger_price = sl_order

                config.tracked_positions[token] = {
                    "quantity": pos["quantity"],
                    "entry_price": pos["average_price"],
                    "sl_order_id": sl_order_id,
                    "side": get_position_side(pos),
                    "best_price": pos["average_price"],
                    "current_trigger": trigger_price
                }

def cancel_order(order_id):
    try:
        kite.cancel_order(
            variety="regular",
            order_id=order_id
        )
    except Exception:
        logger.exception(
            f"Failed to cancel order {order_id}"
        )

def handle_position_size_change(pos):

    token = pos["instrument_token"]

    tracked = config.tracked_positions[token]

    if pos["quantity"] != tracked["quantity"]:

        logger.info(
            f"Quantity changed for "
            f"{pos['tradingsymbol']}"
        )

        current_side = get_position_side(pos)

        if current_side != tracked["side"]:
            logger.warning(
                f"Position side changed for {pos['tradingsymbol']}. "
                "Existing SL transaction type cannot be modified; replacing SL order."
            )

            cancel_order(tracked["sl_order_id"])

            sl_order = place_SL_order(pos)
            if not sl_order:
                logger.warning(
                    f"Unable to place replacement SL for {pos['tradingsymbol']} "
                    "after side change"
                )
                config.tracked_positions.pop(token, None)
                return

            new_sl_order_id, trigger_price = sl_order

            config.tracked_positions[token] = {
                "quantity": pos["quantity"],
                "entry_price": pos["average_price"],
                "sl_order_id": new_sl_order_id,
                "side": current_side,
                "best_price": pos["average_price"],
                "current_trigger": trigger_price
            }
            return

        try:
            kite.modify_order(
                variety="regular",
                order_id=tracked["sl_order_id"],
                quantity=abs(pos["quantity"]),
                order_type="SL-M",
                trigger_price=tracked["current_trigger"]
            )
        except Exception:
            logger.exception(
                f"Failed to modify SL quantity for {pos['tradingsymbol']}"
            )
            return

        config.tracked_positions[token] = {
            "quantity": pos["quantity"],
            "entry_price": pos["average_price"],
            "sl_order_id": tracked["sl_order_id"],
            "side": tracked["side"],
            "best_price": tracked["best_price"],
            "current_trigger": tracked["current_trigger"]
        }

        logger.info(
            f"Modified SL quantity for {pos['tradingsymbol']} "
            f"to {abs(pos['quantity'])}"
        )

def cleanup_closed_positions():

    live_tokens = {
        p["instrument_token"]
        for p in get_open_positions()
    }

    for token in list(config.tracked_positions.keys()):

        if token not in live_tokens:

            logger.info(
                f"Position closed: {token}"
            )
            cancel_order(config.tracked_positions[token]["sl_order_id"])

            config.tracked_positions.pop(token)

            if token in subscribed_tokens and config.kws:
                try:
                    config.kws.unsubscribe([token])
                except Exception:
                    logger.exception(
                        f"Failed to unsubscribe token {token}"
                    )

            subscribed_tokens.discard(token)


def run_manager():
    global kite
    client_id = get_kite_client.get_single_client_id()

    logger.info(
        f"Starting SL manager for {client_id}"
    )

    kite = get_kite_client.get_kite_client()
    time.sleep(2)

    client_doc = get_kite_client.get_client_doc_from_json()

    config.kws = KiteTicker(
        client_doc["api_key"],
        client_doc["access_token"]
    )

    config.kws.on_ticks = on_ticks
    config.kws.on_connect = on_connect
    config.kws.on_close = on_close
    config.kws.on_error = on_error

    config.kws.connect(threaded=True)
    time.sleep(2)

    initial_positions = get_open_positions()
    sync_subscriptions(initial_positions)
    reconcile_existing_sl_orders(initial_positions)

    while True:
        try:
            lockdown = is_lockdown_activated()
            if lockdown:
                logger.info("Lockdown Activated. Shutting SL manager now.")
                sys.exit()

            positions = get_open_positions()
            sync_subscriptions(positions)
            monitor_new_positions(positions)

            for pos in positions:

                token = pos["instrument_token"]

                if token in config.tracked_positions:
                    handle_position_size_change(pos)
                    trail_stop_loss(pos)

            cleanup_closed_positions()

            # Close Risk Manager at 18:00 PM and start fresh the next day.
            now = dt.datetime.now(ist)
            print("Current time: ", now.hour, now.minute)
            if now.hour >= 18:
                logger.info(
                    "Day Complete. No More Trading Allowed. "
                    "Shutting down SL Manager for the day."
                )
                sys.exit()

        except Exception:
            logger.exception("Error in SL manager loop")

        time.sleep(2)


def main():
    if len(sys.argv) > 1:
        logger.error(
            "SL manager runs in single-client mode and does not accept a client id argument."
        )
        sys.exit(1)

    run_manager()


if __name__ == "__main__":
    main()
