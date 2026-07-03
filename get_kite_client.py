from kiteconnect import KiteConnect 
import os 
import sys
import json
import traceback
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

current_file_path = os.path.dirname(os.path.realpath(__file__))


def load_credentials():
    json_file = os.path.join(current_file_path, "credentials.json")
    with open(json_file) as f:
        return json.load(f)


def get_single_client_id():
    credentials = load_credentials()
    client_ids = list(credentials.keys())

    if len(client_ids) != 1:
        logger.error(
            "Single-client mode requires exactly one entry in credentials.json. "
            f"Found {len(client_ids)} entries."
        )
        sys.exit(1)

    return client_ids[0]


def get_client_doc_from_json(client_id=None):
    try:
        if client_id is None:
            client_id = get_single_client_id()

        data = load_credentials()
        return data[client_id]
    except Exception as e:
        logger.error(
            f"Error reading client document for {client_id}: {traceback.format_exc()}"
        )
        return None


def get_kite_client(client_id=None):
    if client_id is None:
        client_id = get_single_client_id()

    client_doc = get_client_doc_from_json(client_id)
    api_key = client_doc["api_key"]
    access_token = client_doc["access_token"]


    # kite=1
    kite = KiteConnect(api_key=api_key)
    try:
        kite.set_access_token(access_token)
        profile = kite.profile()
        logger.info(f"Access Token valid for {client_id}.")
    except Exception:
        logger.error(
            f"Access token expired for {client_id}. Please generate new token manually."
        )
        sys.exit(0)

    return kite
