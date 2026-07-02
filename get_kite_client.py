from kiteconnect import KiteConnect 
import os 
import sys
import json
import traceback
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

current_file_path = os.path.dirname(os.path.realpath(__file__))


def get_client_doc_from_json(client_id):
    try:
        json_file = os.path.join(current_file_path, "credentials.json")
        with open(json_file) as f:
            data = json.load(f)
            # print("data=", data)
            return data[client_id]
    except Exception as e:
        logger.error(
            f"Error reading client document for {client_id}: {traceback.format_exc()}"
        )
        return None


def get_kite_client(client_id):
    api_key = get_client_doc_from_json(client_id)["api_key"]
    api_secret = get_client_doc_from_json(client_id)["secret_key"]
    access_token = get_client_doc_from_json(client_id)["access_token"]


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