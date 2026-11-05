import os
import re
import json
import time
import requests
import urllib3
import pandas as pd
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from dotenv import load_dotenv

# Load variables from a .env file sitting next to this script
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

urllib3.disable_warnings()

# Default Prompt from notebook
# Which kinds of companies the user wants to find.
# Keys are what the UI stores; values are what the AI is told.
TARGET_MODEL_TEXT = {
    "B2B": 'B2B: its main customers are other businesses',
    "B2C": 'B2C: its main customers are individual consumers',
    "Both": 'both: it sells to businesses AND to consumers',
}
DEFAULT_TARGET_TYPES = ["B2B"]


def build_prompt_text(target_types=None, allow_professional_services=False):
    """Builds the AI prompt, with the eligibility rules matching what the user picked."""
    types = [t for t in (target_types or DEFAULT_TARGET_TYPES) if t in TARGET_MODEL_TEXT]
    if not types:
        types = list(DEFAULT_TARGET_TYPES)

    model_list = "; ".join(TARGET_MODEL_TEXT[t] for t in types)

    if allow_professional_services:
        rule2 = ("2. Professional services companies (consulting, marketing or advertising agencies, accounting,\n"
                 "   recruiting, IT consulting, outsourcing, design or dev agencies and similar) ARE allowed.\n"
                 "   Do not reject a company for selling services.\n")
    else:
        rule2 = ("2. It does NOT sell professional services. Professional services means consulting,\n"
                 "   marketing/advertising agencies, accounting, recruiting, IT consulting,\n"
                 "   outsourcing, design/dev agencies, and similar.\n"
                 "   EXCEPTION: medical and legal services are allowed.\n")

    rules = ["- Any industry is fine (manufacturing, SaaS, logistics, construction, retail, etc.)."]
    if "Both" not in types:
        rules.append("- If it sells to both businesses and consumers, mark it NOT eligible.")
    if len(types) == 1 and types[0] == "Both":
        rules.append("- If it sells only to businesses or only to consumers, mark it NOT eligible.")
    rules.append('- If the description is too vague to decide, set eligible to "unclear". Do not guess.')

    clarifications = []
    if not allow_professional_services:
        clarifications.append(
            "- Banks, insurers and other financial institutions sell financial services: verdict is \"Not Qualified\".\n"
            "- IT products, software, cloud and cybersecurity PRODUCTS are NOT professional services.\n"
            "  Only IT consulting, outsourcing and agency work is excluded.")
    clarifications.append(
        "- Government bodies, universities, non-profits and sovereign funds are not commercial\n"
        "  sellers: verdict is \"Not Qualified\".")
    clarifications.append(
        "- Eligibility applies to the TARGET company only. Do not judge the target by what OUR company sells.")

    head = """You are a sales prospecting analyst. You get a TARGET company description and OUR company description.
The descriptions may be in any language. Always answer in English.
Do two steps in order.

STEP 1: ELIGIBILITY CHECK (applies to the TARGET company only)
The target is ELIGIBLE only if BOTH are true:
"""
    body = ("1. Its business model is one of the following: " + model_list + ".\n"
            "   Any other business model is NOT eligible.\n"
            + rule2 + "\n"
            "Rules:\n" + "\n".join(rules) + "\n\n"
            "Eligibility clarifications:\n" + "\n".join(clarifications) + "\n")

    tail = """
STEP 2: VERDICT
- If NOT eligible: verdict is "Not Qualified". In "why", say which eligibility rule it failed.
- If "unclear": verdict MUST be "Needs Review", never "Not Qualified". In "why", say what information is missing.
- If eligible: decide if it is a good prospect for OUR company. Consider:
  need fit (does the target likely have the problem our company solves?),
  offer fit (does what we offer match how the target sells or operates?),
  and timing signals (growth, hiring, expansion, or other signs they would buy now).
  Verdict is "Qualified" only if there is a clear need and a clear offer fit.
  Otherwise verdict is "Not Qualified". Be direct and honest. Do not be generous.
- Do not assume a company needs our service just because it is large. "Qualified" requires a
  specific signal in the description (for example: who its customers are, how it sells,
  or signs of growth or buying activity).

Only use facts from the two descriptions. Do not use outside knowledge about the company.
Do not invent details. If the description does not say what the company sells or who its
customers are, set eligible to "unclear".
Give only your final reasoning in "why". Never write "wait" or re-evaluate inside it.

Return ONLY valid JSON, with no extra text:
{{
  "verdict": "Qualified" | "Not Qualified" | "Needs Review",
  "why": "2-4 sentences explaining the verdict, based only on the descriptions",
  "eligible": true | false | "unclear",
  "business_model": "B2B" | "B2C" | "both" | "unknown",
  "sells_professional_services": true | false,
  "strengths": ["reasons this target is a good fit"],
  "weaknesses": ["reasons this target may not be a good fit"],
  "pitch_angle": "one sentence on the best angle to approach this target (only if verdict is Qualified, else null)"
}}
If NOT eligible: set "strengths", "weaknesses" and "pitch_angle" to null.
If "unclear": set "strengths", "weaknesses" and "pitch_angle" to null.

TARGET COMPANY DESCRIPTION:
{company_description}

OUR COMPANY DESCRIPTION:
{our_company_description}
"""
    return head + body + tail


DEFAULT_OUR_DESC = ""  # no built-in company: the app asks the user for it

def get_env_api_key():
    """Read the Gemini key from the environment (.env). Never hardcode it here."""
    return (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or "").strip()

DEFAULT_API_KEY = get_env_api_key()
DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8"
}
ABOUT_KEYS = ["about-us", "about", "who-we-are", "our-story", "our-company", "company"]
FALLBACK_PATHS = ["/about", "/about-us", "/company", "/who-we-are", "/our-story"]
BLOCKED = ("linkedin.com", "linktr.ee", "facebook.com", "instagram.com", "twitter.com", "x.com")
JUNK = ["cookie", "newsletter", "privacy policy", "all rights reserved", "subscribe", "javascript"]

COLUMN_NAMES = {
    "website": "Website",
    "company_name": "Company Name",
    "source": "Data Source",
    "ai_verdict": "Filter Result",
    "ai_why": "Reasoning",
    "ai_eligible": "Eligibility",
    "ai_business_model": "Business Model",
    "ai_sells_professional_services": "Sells Professional Services",
    "ai_strengths": "Strengths",
    "ai_weaknesses": "Weaknesses",
    "ai_pitch_angle": "Suggested Pitch Angle",
    "ai_error": "AI Error",
    "about_url": "About Page URL",
    "description": "Company Description",
    "employees_mentioned": "Employees",
}

ELIGIBLE_LABELS = {"True": "Eligible", "False": "Not Eligible", "unclear": "Needs Review", True: "Eligible", False: "Not Eligible"}
VERDICT_ORDER = {"Qualified": 0, "Needs Review": 1, "Not Qualified": 3}


def clean_url(u):
    if u is None or pd.isna(u):
        return ""
    s = str(u).strip()
    if s.lower() in ["", "nan", "none", "null", "undefined"]:
        return ""
    return s if s.startswith("http://") or s.startswith("https://") else "https://" + s


def _host(u):
    try:
        return urlparse(u).netloc.lower().replace("www.", "")
    except Exception:
        return ""


def fetch(url, timeout=12):
    try:
        r = requests.get(url, headers=HEADERS, timeout=(5, timeout))
    except requests.exceptions.SSLError:
        r = requests.get(url, headers=HEADERS, timeout=(5, timeout), verify=False)
    r.raise_for_status()
    r.encoding = r.apparent_encoding
    return r.text


def find_about_url(home_url, soup):
    best, best_score = None, 0
    for a in soup.find_all("a", href=True):
        raw = a["href"].strip()
        if raw.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        full = urljoin(home_url, raw)
        if _host(full) != _host(home_url):
            continue
        href, text = raw.lower(), a.get_text(" ", strip=True).lower()
        for i, key in enumerate(ABOUT_KEYS):
            if key in href or key == text:
                score = len(ABOUT_KEYS) - i
                if score > best_score:
                    best, best_score = full, score
    return best


def extract(html):
    soup = BeautifulSoup(html, "lxml")
    # Clean scripts and styles
    for s in soup(["script", "style", "noscript", "svg"]):
        s.decompose()
        
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    meta_text = meta["content"].strip() if meta and meta.get("content") else ""
    paras = [p.get_text(" ", strip=True) for p in soup.find_all(["p", "h1", "h2", "h3", "li"])]
    body = " ".join(p for p in paras if len(p) > 50)[:3500]
    full_text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d[\d,]*\+?)\s+(?:\w+\s){0,2}(?:employees|team members|staff|people)", full_text, re.I)
    m2 = re.search(r"team of\s+(\d[\d,]*\+?)", full_text, re.I)
    employees = (m or m2).group(1) if (m or m2) else None
    return (body or meta_text), employees


def is_usable(text, min_len=100):
    if not text or not isinstance(text, str):
        return False
    t = text.lower()
    return len(text) >= min_len and sum(w in t for w in JUNK) < 3


def scrape_about(url):
    home = clean_url(url)
    if not home:
        raise ValueError("Empty URL")
    if any(b in _host(home) for b in BLOCKED):
        raise ValueError("Not a company website (social/blocked domain)")

    home_html = None
    try:
        home_html = fetch(home)
    except Exception:
        alt = home.replace("https://", "http://") if home.startswith("https://") else home.replace("http://", "https://")
        try:
            home_html, home = fetch(alt), alt
        except Exception as e:
            raise RuntimeError(f"Could not connect: {e}")

    soup = BeautifulSoup(home_html, "lxml")
    about = find_about_url(home, soup)
    if not about:
        for path in FALLBACK_PATHS:
            try:
                candidate = urljoin(home, path)
                about_html = fetch(candidate)
                about = candidate
                desc, emp = extract(about_html)
                return about, desc, emp
            except Exception:
                continue

    if about:
        try:
            desc, emp = extract(fetch(about))
        except Exception:
            desc, emp = extract(home_html)
    else:
        about, (desc, emp) = None, extract(home_html)
        
    return about, desc, emp


class B2BEngine:
    def __init__(self, api_key=DEFAULT_API_KEY, model_name=DEFAULT_MODEL, our_description=DEFAULT_OUR_DESC,
                 target_types=None, allow_professional_services=False):
        self.api_key = api_key or get_env_api_key()
        self.model_name = model_name or DEFAULT_MODEL
        self.our_description = our_description or DEFAULT_OUR_DESC
        self.target_types = target_types or list(DEFAULT_TARGET_TYPES)
        self.allow_professional_services = bool(allow_professional_services)
        self._init_ai()

    def _init_ai(self):
        if not self.api_key:
            self.chain = None
            return
        os.environ["GOOGLE_API_KEY"] = self.api_key
        try:
            llm = ChatGoogleGenerativeAI(model=self.model_name, temperature=0)
            prompt = ChatPromptTemplate.from_template(
                build_prompt_text(self.target_types, self.allow_professional_services)
            )
            self.chain = prompt | llm | JsonOutputParser()
        except Exception as e:
            print(f"Error initializing AI: {e}")
            self.chain = None

    def update_settings(self, api_key, model_name, our_description,
                        target_types=None, allow_professional_services=False):
        self.api_key = api_key or get_env_api_key()
        self.model_name = model_name
        self.our_description = our_description
        self.target_types = target_types or list(DEFAULT_TARGET_TYPES)
        self.allow_professional_services = bool(allow_professional_services)
        self._init_ai()

    def evaluate_company(self, description):
        """Runs the AI qualification on a company description."""
        if not self.chain:
            self._init_ai()
        if not (self.our_description or "").strip():
            raise RuntimeError("Company description is empty. Enter it in Settings before running.")
        if not self.chain:
            raise RuntimeError("Gemini AI client is not configured. Set GOOGLE_API_KEY in your .env file.")

        safe_chain = self.chain.with_retry(stop_after_attempt=3, wait_exponential_jitter=True)
        return safe_chain.invoke({
            "company_description": description,
            "our_company_description": self.our_description
        })

    def process_single(self, row, website_col="website", company_col="Company Name"):
        """
        Scrapes and evaluates a single row dict.
        Returns the enriched row dict.
        """
        raw_url = row.get(website_col)
        url = clean_url(raw_url)
        
        raw_comp = row.get(company_col, "")
        if raw_comp is None or pd.isna(raw_comp) or str(raw_comp).strip().lower() in ["nan", "none", "null"]:
            comp_name = ""
        else:
            comp_name = str(raw_comp).strip()
        
        result = {
            "website": url if url else (str(raw_url).strip() if raw_url is not None and not pd.isna(raw_url) else ""),
            "company_name": comp_name,
            "source": "website",
            "about_url": None,
            "description": None,
            "employees_mentioned": None,
            "ai_verdict": None,
            "ai_why": None,
            "ai_eligible": None,
            "ai_business_model": None,
            "ai_sells_professional_services": None,
            "ai_strengths": None,
            "ai_weaknesses": None,
            "ai_pitch_angle": None,
            "ai_error": None,
        }

        # Keep original columns as well
        for k, v in row.items():
            if k not in result:
                result[k] = v

        if not url:
            result["description"] = "ERROR: Missing or invalid website URL"
            result["ai_verdict"] = "Not Qualified"
            result["ai_why"] = "No valid website URL provided in spreadsheet row."
            return result

        # 1. Scrape
        try:
            about_url, desc, employees = scrape_about(url)
            result["about_url"] = about_url
            result["description"] = desc
            result["employees_mentioned"] = employees
        except Exception as e:
            result["description"] = f"ERROR: {e}"
            result["ai_error"] = str(e)
            result["ai_verdict"] = "Not Qualified"
            result["ai_why"] = f"Could not scrape company website: {e}"
            return result

        # 2. Check usability
        if not is_usable(desc, min_len=80):
            result["ai_verdict"] = "Needs Review"
            result["ai_why"] = "Scraped content is too short or inconclusive to determine business fit."
            result["ai_eligible"] = "unclear"
            return result

        # 3. AI Evaluation
        try:
            ai_out = self.evaluate_company(desc)
            if isinstance(ai_out, dict):
                result["ai_verdict"] = ai_out.get("verdict", "Needs Review")
                result["ai_why"] = ai_out.get("why", "")
                result["ai_eligible"] = ai_out.get("eligible")
                result["ai_business_model"] = ai_out.get("business_model")
                result["ai_sells_professional_services"] = ai_out.get("sells_professional_services")
                
                strengths = ai_out.get("strengths")
                result["ai_strengths"] = "; ".join(map(str, strengths)) if isinstance(strengths, list) else strengths
                
                weaknesses = ai_out.get("weaknesses")
                result["ai_weaknesses"] = "; ".join(map(str, weaknesses)) if isinstance(weaknesses, list) else weaknesses
                
                result["ai_pitch_angle"] = ai_out.get("pitch_angle")
            else:
                result["ai_error"] = str(ai_out)[:200]
                result["ai_verdict"] = "Needs Review"
                result["ai_why"] = "AI returned an unexpected format."
        except Exception as e:
            result["ai_error"] = str(e)[:300]
            result["ai_verdict"] = "Needs Review"
            result["ai_why"] = f"AI qualification error: {e}"

        return result


def export_records_to_dataframe(records, original_df=None, website_col="website"):
    """
    Combines records into a professionally formatted DataFrame ready for Excel or CSV export.
    """
    prog = pd.DataFrame(records)
    if prog.empty:
        return pd.DataFrame()

    if original_df is not None and not original_df.empty:
        out = original_df.copy()
        # Merge on website column
        cleaned_urls = out[website_col].astype(str).str.strip()
        prog["_merge_key"] = prog["website"].astype(str).str.strip()
        out["_merge_key"] = cleaned_urls
        
        merged = out.merge(prog.drop_duplicates("_merge_key"), on="_merge_key", how="left", suffixes=("", "_enriched"))
        merged.drop(columns=["_merge_key"], inplace=True, errors="ignore")
        final_df = merged
    else:
        final_df = prog

    # Map readable labels
    if "ai_eligible" in final_df.columns:
        final_df["ai_eligible"] = final_df["ai_eligible"].map(
            lambda v: ELIGIBLE_LABELS.get(str(v), v) if pd.notna(v) else v
        )

    # Sort records: Qualified -> Needs Review -> Not Qualified -> Others
    if "ai_verdict" in final_df.columns:
        rank = final_df["ai_verdict"].map(VERDICT_ORDER).fillna(2)
        final_df = final_df.assign(_rank=rank).sort_values("_rank", kind="stable").drop(columns="_rank")

    # Column ordering: key columns first
    first_cols = ["company_name", "website", "ai_verdict", "ai_why", "ai_eligible", 
                  "ai_pitch_angle", "ai_strengths", "ai_weaknesses", "ai_business_model", 
                  "about_url", "employees_mentioned", "description", "ai_error"]
    
    existing_first = [c for c in first_cols if c in final_df.columns]
    other_cols = [c for c in final_df.columns if c not in existing_first]
    final_df = final_df[existing_first + other_cols]

    # Rename to clean professional headers
    final_df = final_df.rename(columns=COLUMN_NAMES)
    return final_df
