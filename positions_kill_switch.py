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
import config
import get_kite_client


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ist = pytz.timezone("Asia/Kolkata")

logger = get_logger.get_logger("Risk_Manager")

current_file_path = os.path.dirname(os.path.realpath(__file__))
subscribed_tokens=set()


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


def today_ist():
    return dt.datetime.now(ist).strftime(
        "%Y-%m-%d"
    )


def now_ist_string():
    return dt.datetime.now(ist).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def save_risk_state(state):

    with open(RISK_STATE_FILE, "w") as f:
        json.dump(
            state,
            f,
            indent=4
        )


def get_client_risk_state(state, client_id):
    clients = state.setdefault(
        "clients",
        {}
    )

    return clients.setdefault(
        client_id,
        {
            "peak_mtm": 0
        }
    )


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

    cancel_all_orders(kite)
    exit_all_positions(kite)


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
            cancel_all_orders(kite)
            exit_all_positions(kite)

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

def update_peak_mtm(mtm, client_id):

    state = load_risk_state()
    client_state = get_client_risk_state(
        state,
        client_id
    )

    peak = client_state.get(
        "peak_mtm",
        0
    )

    if mtm > peak:

        client_state["peak_mtm"] = mtm
        state["peak_mtm"] = max(
            state.get("peak_mtm", 0),
            mtm
        )

        save_risk_state(state)

        logger.info(
            f"{client_id} New Peak MTM={mtm}"
        )


def check_profit_protection(mtm, opening_balance, client_id):
    state = load_risk_state()
    client_state = get_client_risk_state(
        state,
        client_id
    )

    config.PEAK_MTM = client_state.get(
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
    for tick in ticks:
        config.live_ltp_dict[tick["instrument_token"]]=tick["last_price"]
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

def start_websocket(client_id):

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
            "WebSocket not connected. Calculating MTM with position last_price fallback."
        )

    total_mtm=0.0

    for pos in positions:
        token = pos["instrument_token"]
        current_price = config.live_ltp_dict.get(token, pos["last_price"])

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
    except Exception as e:
        logger.error(f"Error calculating MTM: {traceback.format_exc()}")
        return 0, 0, 0


def cancel_all_orders(kite):

    orders = kite.orders()

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


def exit_all_positions(kite):
    # print("Exiting all positions...")
    try:
        positions = kite.positions()
        open_positions = [
            position for position in positions["net"] if position["quantity"] != 0
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
        opening_balance * 0.07,
    )

    logger.info(
        f"Opening Balance: {opening_balance}"
    )
    logger.info(
        f"Daily Loss Limit: {daily_loss_limit}"
    )


    return daily_loss_limit

def load_credentials():
    json_file = os.path.join(current_file_path, "credentials.json")
    with open(json_file) as f:
        return json.load(f)


def run_engine(client_id):

    logger.info(
        f"Starting Restart-Safe Trading Risk Engine for {client_id}"
    )
    global kite

    kite = get_kite_client.get_kite_client(client_id)
    start_websocket(client_id)
    config.kws.connect(threaded=True)
    time.sleep(2)
    
    SL_manager_path = os.path.join(
        current_file_path,
        "SL_Manager.py"
    )

    log_file = open(os.path.join(current_file_path, "SL_manager.log"), "a")

    process = subprocess.Popen(
        [sys.executable, SL_manager_path, client_id],
        stdout=log_file,
        stderr=log_file,
        start_new_session=True
    )

    try:
        opening_balance = get_opening_balance(kite)
        loss_threshold = ensure_daily_threshold(kite, opening_balance)
        while True:
            positions = get_all_positions()
            sync_subscriptions()

            #Close Risk Manager at 18:00 PM and start fresh the next day. This is to avoid any issues with the API or the system. We can also do this to avoid any issues with the market data or the orders. This will also help us to start fresh the next day and avoid any issues with the previous day's data.
            now = dt.datetime.now(ist)
            print("Current time: ", now.hour, now.minute)
            if now.hour >= 18:
                logger.info("Day Complete. No More Trading Allowed. Shutting down Risk Manager for the day. ")
                sys.exit()

            today = today_ist()
            state = load_risk_state()
            if state["date"] != today:
                state = {
                    "date": today,
                    "lockdown": False,
                    "reason": "",
                    "peak_mtm": 0,
                    "kill_switch_triggered": False,
                    "clients": {}
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
                MTM, order_len, open_orders = check_kill_condition(kite,positions)
                update_peak_mtm(MTM, client_id)

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


            except Exception:
                logger.error(
                    f"Error processing {client_id}:\n{traceback.format_exc()}"
                )
                sys.exit()
            # print("Sleeping for {} seconds...".format(config.CHECK_INTERVAL), "\n\n\n\n\n")
            # Maintain fixed interval
            elapsed = time.time() - start_time
            sleep_time = config.CHECK_INTERVAL - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("Engine stopped manually.")
        sys.exit()


def run_all_clients():
    credentials = load_credentials()

    if len(sys.argv) > 1:
        client_id = sys.argv[1]
        if client_id not in credentials:
            logger.error(
                f"Unknown client id for Risk Manager: {client_id}"
            )
            sys.exit(1)
        run_engine(client_id)
        return

    client_ids = list(credentials.keys())

    if len(client_ids) == 1:
        run_engine(client_ids[0])
        return

    processes = []
    for client_id in client_ids:
        processes.append(
            subprocess.Popen(
                [sys.executable, __file__, client_id],
                start_new_session=True
            )
        )

    try:
        for process in processes:
            process.wait()
    except KeyboardInterrupt:
        for process in processes:
            process.terminate()
        raise


if __name__ == "__main__":
    run_all_clients()
    
