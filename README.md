# Choumran

A web app that detects **fraud** and **scams** in money transactions with machine learning, and **maps** the links between persons and shops.

The interface is in **English** by default, and you can switch it to **العربية** (right-to-left) or **Français** from the 🌐 menu. The choice is remembered in the browser.

## What it does

1. **Private data:** upload a transactions file (CSV or Excel), or generate synthetic data. Data is processed in memory only and never saved on the server.
2. **Upload / implement:** map your file's columns to the expected fields. The app then computes behavioural signals for every transaction:
   - amount vs. the sender's usual
   - hour of day
   - new device or city
   - bursts of transactions
   - a new receiver collecting money from many people
   - …
3. **Charts and patterns:** a dashboard with charts, plus a Random Forest model that classifies each transaction as:
   - **ordinary**
   - **fraud:** stolen account or card, e.g. large night-time spending from a new device
   - **scam:** victims paying a fake receiver

   An adjustable **T/F threshold** decides which transactions are flagged as suspicious.
4. **Compare data with patterns:** if your file has a `label` column, the app compares its results against it (accuracy, recall, confusion matrix). You can also train a new model on your own labels.
5. **Merge results and map:** an interactive person ↔ shop network. Every entity gets a risk score and a role, such as *scam suspect*, *possibly compromised account* or *possible scam victim*.

## Running it

You need Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000

On the first run the default model trains on synthetic data. This takes about 15 seconds, and the model is then cached in `models/`.

To open the app from a phone on the same Wi-Fi network, run `HOST=0.0.0.0 python app.py` and visit `http://<computer-ip>:5000`.

## File format

| Column | Meaning | |
|---|---|---|
| `timestamp` | Date & time | required |
| `sender_id` | Sender ID (person) | required |
| `receiver_id` | Receiver ID (shop or person) | required |
| `amount` | Amount | required |
| `transaction_id`, `sender_name`, `receiver_name` | ID and names | optional |
| `receiver_type` | `shop` or `person` | optional |
| `device_id`, `city` | Device and location (improve detection) | optional |
| `label` | `ordinary` / `fraud` / `scam` | optional (comparison / training) |

Column names don't have to match: the app guesses common English, French and Arabic names, and you can map the rest by hand. You can download a sample file from the first page.

## A note on accuracy

The default model is trained on **synthetic data**, where the patterns are clear. The high scores it gets there do **not** mean it will be as accurate on real data. For real data:

- add a `label` column for known fraud and scam cases, and retrain on it
- review the results manually before acting against any person or shop

**Results are leads to review, not proof.**

## Translations

All interface text lives in `static/i18n/en.json`, `ar.json` and `fr.json`. The browser and the server (error messages, CSV exports) share these files. To change a wording, edit the matching key. To add a language, copy `en.json`, translate it, and add the code to `LANGS` in `fraudnet/i18n.py` and `static/js/app.js`.

## Project layout

```
app.py                 Flask server and API
fraudnet/generator.py  synthetic data generator (names per language)
fraudnet/schema.py     column detection and data cleaning
fraudnet/features.py   behavioural features (past-only)
fraudnet/model.py      training and evaluation
fraudnet/analysis.py   classification, entity risk, network
fraudnet/i18n.py       server-side translations
static/i18n/           translation files (en / ar / fr)
templates/, static/    interface (mobile-friendly, LTR + RTL)
tests/                 tests: python -m pytest
```
