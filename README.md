# AI Lead Filter

A desktop app that filters a list of companies down to the ones worth contacting, based on the kind of company you are looking for.

You give it a spreadsheet with company websites. It visits each site, reads the About page, and asks Google's Gemini model whether that company is a good fit for what you sell. You get back a verdict and a short explanation for every company, plus a suggested way to approach the good ones.

I built it to stop myself from opening hundreds of websites by hand.

## What it does

1. Loads an Excel or CSV file of companies.
2. Visits each company's website and looks for an About page. If it can't find one, it falls back to the homepage.
3. Sends the text it found, along with your own company description, to Gemini.
4. Shows a verdict for each company:
   - **Qualified**: clear need and a clear match with what you offer.
   - **Needs Review**: not enough information to decide, so a human should look.
   - **Not Qualified**: it failed a rule or isn't a good fit.
5. Lets you search, filter, open a detailed view for any company, and export the results to Excel or CSV.

### Choosing which companies you want

The app asks you which kind of companies you are looking for. Tick one or more:

- **B2B**: companies that sell to other businesses
- **B2C**: companies that sell to individual consumers
- **Both**: companies that sell to businesses and consumers

There is also a checkbox to include professional services companies (consulting, agencies, recruiting and similar). It is off by default, which means those companies are rejected. Medical and legal services are always allowed.

Anything that doesn't match what you ticked is marked Not Qualified, and the reasoning explains which rule it failed. Banks, insurers, government bodies, universities and non-profits are also marked Not Qualified.

The AI prompt is built from your choices at run time. The wording lives in `b2b_engine.py` if you ever want to adjust it.

## What you need

- Python 3 installed on your computer
- A Google Gemini API key (free to create in [Google AI Studio](https://aistudio.google.com/), though check Google's current limits and pricing yourself)
- Internet access, since it visits websites and calls Gemini

## How to run it

**1. Get the files**

Put `app.py`, `b2b_engine.py` and `requirements.txt` in the same folder.

**2. Open a terminal in that folder and make a virtual environment (recommended)**

Windows:
```
python -m venv venv
venv\Scripts\activate
```

Mac or Linux:
```
python3 -m venv venv
source venv/bin/activate
```

**3. Install the packages**

```
pip install -r requirements.txt
```

**4. Add your API key**

Create a file named `.env` in the same folder (just `.env`, nothing before the dot) and put this inside:

```
GOOGLE_API_KEY=paste-your-key-here
```

There is also an optional line if you want a different model:

```
GEMINI_MODEL=model-name-here
```

Never share this file or upload it anywhere public. If you use Git, add `.env` to your `.gitignore`.

**5. Start the app**

```
python app.py
```

On the first launch it will ask you to describe your company and tick which types of companies you want to find. For the description, write what you sell, who you sell to, and what problem you solve. The more specific you are, the better the verdicts will be. It saves your answers, and you can change both later in Settings.

## How to use it

1. Click **Browse Sheet** and pick your `.xlsx`, `.xls` or `.csv` file.
2. Check the **Website Column**. The app tries to guess it, but pick the right one if it guessed wrong. The Company Name column is optional.
3. Choose how many rows to process. Starting with the first 25 is a good way to test your setup before running a big list.
4. Click **Start AI Screener**. You can pause or stop at any time.
5. Results appear as they finish. Use the filter and search box to narrow them down. Double-click a row, or press **View Dossier**, to see the full reasoning, strengths, weaknesses and suggested pitch.
6. Export with **Export Filtered View** or **Export All**. Excel exports are color coded by verdict.

The table shows 25 rows at a time. Press **Show More** to see the next batch.

## Your spreadsheet

Your file needs one column with company websites. A column with company names is helpful but not required. Your other columns are kept and come back in the export next to the new AI columns.

## Files in this project

| File | What it is |
|---|---|
| `app.py` | The window, buttons and tables (PyQt5) |
| `b2b_engine.py` | Website scraping and the Gemini evaluation |
| `.env` | Your API key. You create this, and it stays private |
| `config.json` | Created by the app to remember your settings. It never stores the API key |
| `crash_log.txt` | Created only if the app hits an unexpected error |

## Things to know before you trust it

- **It only knows what it can read.** The verdict is based on the text it pulled from the website, nothing else. If a site is mostly images or loads its content with JavaScript, the app may not read much, and it will usually mark that company Needs Review.
- **It will get some wrong.** An AI is giving an opinion here. Treat Qualified as "worth a closer look," not as a guarantee, and spot-check the results before you send anything to anyone.
- **Social sites are skipped.** LinkedIn, Facebook, Instagram and similar pages aren't treated as company websites.
- **Companies are processed one after another.** The Concurrency field in Settings exists, but the current version does not use it to run several at once, so big lists take a while.
- **Be considerate.** You are visiting other people's websites automatically and using your own API quota. Run small batches first.

## If something goes wrong

- **"Gemini AI client is not configured"**: your `.env` file is missing, in the wrong folder, or the key name is wrong. It must be `GOOGLE_API_KEY`.
- **Every row has an AI error**: the key may be invalid, out of quota, or the model name is wrong. Try the default model.
- **Could not open spreadsheet**: for old `.xls` files you may need to run `pip install xlrd`.
- **The app closes or shows an error box**: look at `crash_log.txt` in the project folder.
- **A company says it could not connect**: the site may be down, block automated visits, or the URL in your sheet may be wrong.
