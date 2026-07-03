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

    for tick in ticks:

        config.live_ltp_dict[
            tick["instrument_token"]
        ] = tick["last_price"]


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

def get_open_positions():
    positions = kite.positions()
    return [p for p in positions["net"] if p["quantity"]>0]

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
    entry_price = position["average_price"]
    config.trigger_price =  round(entry_price*(1-config.SL_PERCENT/100),1)
    if config.trigger_price >= config.live_ltp_dict.get(token, position["last_price"]):

        logger.warning(f"""Cannot place SL
        Trigger : {config.trigger_price}
        LTP     : {config.live_ltp_dict}
        """
        )
        return None
    else:

        order_id = kite.place_order (
            variety="regular",
            exchange=position["exchange"],
            tradingsymbol=position["tradingsymbol"],
            transaction_type="SELL",
            quantity=position["quantity"],
            product=position["product"],
            order_type="SL-M",
            trigger_price=config.trigger_price,
            market_protection=-1)
        
        logger.info(
            f"SL placed for {position['tradingsymbol']} "
            f"at {config.trigger_price}"
        )
        return order_id

def trail_stop_loss(pos):

    token = pos["instrument_token"]

    tracked = config.tracked_positions[token]

    current_price = config.live_ltp_dict.get(
    token, pos["last_price"])

    if current_price is None:
        return

    # New High
    if current_price > tracked["highest_price"]:

        tracked["highest_price"] = current_price

        new_trigger = round(
            current_price *
            (1 - config.TRAILING_SL_PERCENT / 100),
            1
        )

        # Move SL only upward
        if new_trigger > tracked["current_trigger"]:

            logger.info(
                f"{pos['tradingsymbol']} | "
                f"Peak={current_price} | "
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
                    f"Modified SL from "
                    f"{config.trigger_price} to {new_trigger}"
                )

            except Exception as e:

                logger.exception(
                    f"Failed trailing SL for "
                    f"{pos['tradingsymbol']}"
                )

def monitor_new_positions(positions):
    for pos in positions:

        token = pos["instrument_token"]

        if token not in config.tracked_positions:

            sl_order_id = place_SL_order(pos)
            if sl_order_id:

                config.tracked_positions[token] = {
                    "quantity": pos["quantity"],
                    "entry_price": pos["average_price"],
                    "sl_order_id": sl_order_id,
                    "highest_price": pos["average_price"],
                    "current_trigger": config.trigger_price
                }

def cancel_order(order_id):
    try:
        kite.cancel_order(
            variety="regular",
            order_id=order_id
        )
    except Exception as e:
        logger.info(e)

def handle_position_size_change(pos):

    token = pos["instrument_token"]

    tracked = config.tracked_positions[token]

    if pos["quantity"] != tracked["quantity"]:

        logger.info(
            f"Quantity changed for "
            f"{pos['tradingsymbol']}"
        )

        cancel_order(tracked["sl_order_id"])

        new_sl_order_id = place_SL_order(pos)

        config.tracked_positions[token] = {
            "quantity": pos["quantity"],
            "entry_price": pos["average_price"],
            "sl_order_id": new_sl_order_id,
            "highest_price": tracked["highest_price"],
            "current_trigger": tracked["current_trigger"]
        }

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
            subscribed_tokens.discard(token)


global kite

json_file = os.path.join(current_file_path, "credentials.json")
with open(json_file) as f:
    credentials = json.load(f)
    client_id = credentials.keys()
    for client_id in credentials.keys():
        kite = get_kite_client.get_kite_client(client_id)
        time.sleep(2)

api_key = get_kite_client.get_client_doc_from_json(
    client_id
)["api_key"]

access_token = get_kite_client.get_client_doc_from_json(
    client_id
)["access_token"]

config.kws = KiteTicker(
    api_key,
    access_token
)

config.kws.on_ticks = on_ticks
config.kws.on_connect = on_connect

config.kws.connect(threaded=True)
time.sleep(2)

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

        #Close Risk Manager at 18:00 PM and start fresh the next day. This is to avoid any issues with the API or the system. We can also do this to avoid any issues with the market data or the orders. This will also help us to start fresh the next day and avoid any issues with the previous day's data.
        now = dt.datetime.now(ist)
        print("Current time: ", now.hour, now.minute)
        if now.hour >= 18:
            logger.info("Day Complete. No More Trading Allowed. Shutting down Risk Manager for the day. ")
            sys.exit()

    except Exception as e:
        logger.info("Error:", e)

    time.sleep(2)