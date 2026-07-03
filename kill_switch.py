from selenium import webdriver
from selenium.webdriver.common.by import By
import traceback
import mintotp
import time
import sys
import get_kite_client


def get_client_doc_from_json(client_id=None):
    try:
        return get_kite_client.get_client_doc_from_json(client_id)
    except Exception:
        traceback.print_exc()


def get_totp(userid):
    totp_key = get_client_doc_from_json(userid)["totp_key"]
    totp = mintotp.totp(totp_key)
    return totp


def create_driver():
    options = webdriver.ChromeOptions()
    options.binary_location = "/usr/bin/google-chrome"

    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")

    return webdriver.Chrome(options=options)


def disable_segment(client_id, driver):
    try:
        password = get_client_doc_from_json(client_id)["password"]
        #! Get to the console - segment activation page
        driver.get("https://console.zerodha.com/account/segment-activation")
        time.sleep(2)

        #! Login
        driver.find_element(by=By.ID, value="userid").send_keys(client_id)
        time.sleep(1)
        driver.find_element(by=By.ID, value="password").send_keys(password)
        time.sleep(1)
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[1]/div/div/div[1]/div/div/div/form/div[4]/button",
        ).click()
        time.sleep(1)
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[1]/div/div/div[1]/div[2]/div/div/form/div[1]/input",
        ).send_keys(get_totp(client_id))
        time.sleep(5)

        #! Disable the NSE-FO segment.
        #! To add different segments, you can always copy xpath from inspecting element and replace below xpath.
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[2]/div[2]/div/div/div/div[2]/div[1]/div[2]/div[4]/div[1]/div[2]/div/div/div/div[3]/div/div/div/label",
        ).click()
        time.sleep(1)

        # Disable th BSE-FO sengment
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[2]/div[2]/div/div/div/div[2]/div[1]/div[2]/div[4]/div[1]/div[2]/div/div/div/div[5]/div/div/div/label",
        ).click()
        time.sleep(1)

        # Disable the Commodity sengment
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[2]/div[2]/div/div/div/div[2]/div[1]/div[2]/div[4]/div[1]/div[2]/div/div/div/div[4]/div/div/div/label",
        ).click()
        time.sleep(1)

        #! Clicking on continue
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[2]/div[2]/div/div/div/div[2]/div[1]/div[2]/div[4]/div[1]/button",
        ).click()
        time.sleep(5)

        #! Clicking on confirm-page continue button
        driver.find_element(
            by=By.XPATH,
            value="/html/body/div[2]/div[2]/div/div/div[2]/div/div/div/div/form/div[2]/button[2]",
        ).click()
        time.sleep(10)

    except Exception:
        traceback.print_exc()


def main():
    if len(sys.argv) > 1:
        print(
            "Kill switch runs in single-client mode and does not accept a client id argument."
        )
        sys.exit(1)

    client_id = get_kite_client.get_single_client_id()
    driver = create_driver()
    try:
        disable_segment(client_id, driver)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
