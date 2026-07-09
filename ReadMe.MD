# Kite Kill Switch Project
A simple kill switch for a kite to prevent overtrading.

## Description
This project is a simple kill switch for a kite. It is designed to stop overtrading. The kill switch will check if you have any open positions and if the total loss is above a specified threshold. If the loss is above the threshold, the kill switch will close all positions, cancel all orders, and activate the kill switch. The kill switch will wait for a specified time before checking again. This project is designed to prevent overtrading and protect your capital.

## Language
- Python 3.8

## Libraries
- kiteconnect
- pandas
- json
- time
- datetime
- os
- sys
- logging
- selenium

## Installation
1. Clone the repository
2. Add `position_kill_switch.py` to your task scheduler or crontab.

## Configuration
A template configuration file (`credentials_template.json`) is provided in the repository. You can copy the template and rename it to `credentials.json`. Then you can modify the configuration file to suit your needs.

This project runs in single-client mode. `credentials.json` must contain exactly one client entry. If more than one client is configured, the login app, risk manager, SL manager, and kill switch will stop with an error.

> You need to have the necessary API credentials to run the script. You can get the API credentials from the [Kite Connect](https://kite.trade/).

> TOTP must be enabled in your Kite account for this project to work.

> Please ensure that you have the necessary API credentials in the `credentials.json` file before running the script. Without the correct credentials, the script will not be able to access the necessary data and perform the required actions.

## Usage
1. Run `login_kite.py` to refresh the access token and start the risk manager, or run `positions_kill_switch.py` directly after a valid access token exists.
2. The script will check if you have any open positions.
3. Then the script will check if the total loss is above threshold.
4. If the loss is above threshold, the script will close positions, cancel all orders and activate the kill switch.
5. Between 10:45 AM and 12:45 PM IST, the script will cancel active orders and close positions opened during that restricted window.
6. The script limits deployed capital to 20% of the opening balance by cancelling oversized new-entry orders and closing positions if open capital is already above the cap.
7. The script will wait for a specified time before checking again.

## Alt Usage
1. You can also directly run the `kill_switch.py` script to manually activate the kill switch.
2. This script will skip the checks and wont cancel orders or close positions, but directly activate the kill switch.

## Author
Hi, I'm `Pranav Meher`, the author of this project.
I am a skilled software developer with a strong background in algorithmic trading. I have a deep understanding of the financial markets and have successfully implemented numerous trading strategies. I am passionate about creating reliable solutions to prevent overtrading and am dedicated to continuously improving this kill switch project. I welcome feedback and contributions from the community.

You can connect with me or reach out to me to learn more about my work and stay updated on the latest developments.
- [LinkedIn](https://www.linkedin.com/in/pranavmeher/) 
- [GitHub](https://github.com/Pranavoro)
- [Email](mailto:meherpranav5@gmail.com)
- [Twitter](https://twitter.com/pranav_meher)
- [Upwork](https://www.upwork.com/freelancers/~01bae9d3b236500043)
- [Buy me a coffee ☕️](https://buymeacoffee.com/pranavoro)

## Contributing
Contributions are welcome! If you have any ideas, suggestions, or bug reports, please open an issue or submit a pull request.

## License
This project is licensed under the GNU GENERAL PUBLIC LICENSE - see the [LICENSE](LICENSE.txt) file for details.
