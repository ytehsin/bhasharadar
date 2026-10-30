"""Skill / tool / domain / language taxonomy used by the resume parser and the
matching engine. Every entry is a canonical name with regex synonyms so that
"English>Hindi", "EN-HI" and "Hindi translation" all count as the same skill.
Edit this file to teach the app new vocabulary.
"""
from __future__ import annotations

import re
from functools import lru_cache

# group -> canonical -> patterns
SKILLS: dict[str, dict[str, list[str]]] = {
    "language_services": {
        "Translation": [r"translat\w*", r"\ben\s*[-–>→/]\s*hi\b", r"\bhi\s*[-–>→/]\s*en\b", r"english\s*(to|>|–|-|→)\s*hindi", r"hindi\s*(to|>|–|-|→)\s*english"],
        "Localization": [r"locali[sz]\w*", r"\bl10n\b", r"internationali[sz]ation", r"\bi18n\b"],
        "Transcreation": [r"transcreat\w*"],
        "Linguistic QA (LQA)": [r"\blqa\b", r"linguistic (quality|qa|review|sign[- ]?off|testing|validation)", r"language quality (assurance|review|check)"],
        "Proofreading": [r"proof[- ]?read\w*"],
        "Editing": [r"\bediting\b", r"\beditor\b", r"copy[- ]?edit\w*", r"post[- ]?edit\w*", r"\bmtpe\b", r"machine translation post"],
        "Terminology management": [r"terminolog\w*", r"glossar\w*", r"term ?base", r"style ?guide"],
        "Transcription": [r"transcri(be|ption|bing|ber)\w*"],
        "Subtitling": [r"subtit\w*", r"closed caption\w*", r"\bsrt\b"],
        "Language review": [r"language review\w*", r"linguistic review\w*", r"\breviewer\b", r"language specialist", r"\blinguist\b"],
        "Content writing": [r"content writ\w*", r"copywrit\w*", r"\bblog\w*", r"article writ\w*", r"technical writ\w*"],
        "Journalism": [r"journalis\w*", r"reporter", r"news ?(desk|room|writing)", r"sub[- ]?editor", r"mass communication"],
    },
    "ai_data": {
        "AI training": [r"\bai (trainer|training|tutor)\b", r"model training data", r"\brlhf\b", r"human feedback", r"\bsft\b", r"instruction tuning", r"preference (data|ranking)"],
        "LLM / AI output evaluation": [r"\b(llm|model|ai|chatbot)[- ](output|response)s?\b", r"response (evaluat|rating|quality)\w*", r"\bevaluat(or|ion|e|ing)\b", r"\brater\b", r"side[- ]by[- ]side", r"\bsxs\b", r"grad(e|ing) (ai|model|responses)"],
        "Data annotation": [r"annotat\w*", r"data label(l)?ing", r"\blabel(l)?ing\b", r"\btagging\b", r"\bner\b", r"named entity", r"intent classification", r"sentiment (analysis|tagging|label)"],
        "Multilingual dataset evaluation": [r"multilingual (data|dataset|evaluat\w*|corpus|corpora)", r"dataset (evaluat|quality|review|validation)\w*", r"data (validation|collection)"],
        "Prompt writing": [r"prompt (writ|engineer|creat|design)\w*", r"writ(e|ing) prompts"],
        "Red teaming": [r"red[- ]?team\w*", r"adversarial (prompt|test)\w*", r"jailbreak\w*"],
        "Search quality evaluation": [r"search (quality|relevance|evaluat)\w*", r"ads? (quality|evaluat)\w*", r"relevance (rating|judg)\w*", r"internet (assessor|analyst)"],
        "Speech / audio data": [r"\bspeech\b", r"\baudio\b", r"\basr\b", r"\btts\b", r"voice (assistant|data|recording)", r"utterance\w*", r"pronunciation"],
        "Content moderation / AI safety": [r"content (moderat|review|policy)\w*", r"trust (and|&) safety", r"\bai safety\b", r"harmful content", r"policy (enforcement|labeling)"],
        "NLP data quality": [r"\bnlp\b", r"natural language processing", r"computational linguist\w*", r"corpus linguist\w*"],
    },
    "coordination": {
        "Quality control": [r"quality (assurance|control|check)\w*", r"\bqa\b", r"\bqc\b", r"error (categori[sz]ation|typology|annotation)", r"\bmqm\b", r"\bdqf\b"],
        "Project coordination": [r"project (coordinat|manag)\w*", r"\bpmo?\b", r"delivery (coordinat|manag)\w*", r"workflow manag\w*"],
        "Team lead / reviewer lead": [r"\blead (reviewer|linguist|translator|annotator|evaluator)\b", r"team lead\w*", r"\bmentor\w*", r"\btrain(ed|ing) (new )?(linguists|reviewers|annotators|team)"],
        "Vendor management": [r"vendor (manag|coordinat)\w*", r"resource manag\w*", r"freelancer (manag|coordinat)\w*"],
        "Localization testing": [r"locali[sz]ation (test|qa)\w*", r"linguistic test\w*", r"\bl10n (test|qa)\b", r"\bui (test|review)\w*", r"in[- ]context review"],
    },
}

TOOLS: dict[str, list[str]] = {
    "SDL/RWS Trados": [r"\btrados\b"],
    "memoQ": [r"\bmemoq\b"],
    "Smartcat": [r"\bsmartcat\b"],
    "Phrase (Memsource)": [r"\bmemsource\b", r"\bphrase (tms|strings)\b"],
    "XTM": [r"\bxtm\b"],
    "Wordfast": [r"\bwordfast\b"],
    "MateCat": [r"\bmatecat\b"],
    "Crowdin": [r"\bcrowdin\b"],
    "Lokalise": [r"\blokalise\b"],
    "Transifex": [r"\btransifex\b"],
    "Smartling": [r"\bsmartling\b"],
    "Xbench": [r"\bx-?bench\b"],
    "Verifika": [r"\bverifika\b"],
    "QA Distiller": [r"qa distiller"],
    "CAT tools": [r"\bcat tools?\b", r"computer[- ]assisted translation", r"translation memor\w*", r"\btm\b"],
    "Machine translation": [r"machine translation", r"\bmt\b", r"\bnmt\b", r"google translate", r"\bdeepl\b"],
    "Label Studio": [r"label studio"],
    "Labelbox": [r"\blabelbox\b"],
    "Microsoft Excel": [r"\bexcel\b", r"spreadsheet\w*"],
    "Google Workspace": [r"google (sheets|docs|workspace|drive)"],
    "JIRA": [r"\bjira\b"],
    "Subtitle Edit / Aegisub": [r"subtitle edit", r"\baegisub\b"],
    "Hindi typing / Unicode": [r"\bunicode\b", r"\bmangal\b", r"kruti ?dev", r"inscript", r"hindi typing", r"devanagari"],
    "MS Word": [r"\bms word\b", r"microsoft word"],
}

DOMAINS: dict[str, list[str]] = {
    "Fintech": [r"fintech", r"\bpayments?\b", r"\bupi\b", r"digital wallet", r"\blending\b"],
    "Banking": [r"\bbank(ing)?\b"],
    "Insurance": [r"insurance", r"insurtech"],
    "Education": [r"\bedu(cation|tech|cational)\b", r"e-?learning", r"curricul\w*"],
    "Technology / software": [r"\bsoftware\b", r"\bsaas\b", r"\bapps?\b", r"\btech(nology)?\b", r"\bui\b"],
    "Media / entertainment": [r"\bmedia\b", r"entertainment", r"\bott\b", r"broadcast\w*", r"\bfilm\b", r"publishing"],
    "E-commerce": [r"e-?commerce", r"retail", r"marketplace"],
    "Healthcare / life sciences": [r"health ?care", r"medical", r"pharma\w*", r"clinical", r"life sciences"],
    "Legal": [r"\blegal\b", r"contracts?\b", r"\blaw\b"],
    "Gaming": [r"\bgam(e|es|ing)\b"],
    "Marketing / advertising": [r"marketing", r"advertis\w*", r"\bbrand\w*"],
    "Government / public sector": [r"government", r"public sector", r"ministry"],
    "AI / machine learning": [r"\bai\b", r"artificial intelligence", r"machine learning", r"\bllms?\b", r"generative"],
}

LANGUAGES = [
    "Hindi", "English", "Urdu", "Punjabi", "Bengali", "Bangla", "Marathi", "Gujarati", "Tamil", "Telugu", "Kannada",
    "Malayalam", "Odia", "Oriya", "Assamese", "Sanskrit", "Bhojpuri", "Maithili", "Nepali", "Sindhi", "Konkani",
    "Kashmiri", "Haryanvi", "Rajasthani", "Awadhi", "French", "German", "Spanish", "Portuguese", "Italian", "Dutch",
    "Russian", "Arabic", "Persian", "Farsi", "Turkish", "Japanese", "Korean", "Chinese", "Mandarin", "Cantonese",
    "Thai", "Vietnamese", "Indonesian", "Malay", "Swahili", "Hebrew", "Polish", "Ukrainian", "Greek", "Tagalog",
    "Filipino", "Sinhala", "Burmese", "Khmer",
    # European & others (needed so e.g. "English to Latvian" is recognised as a different language)
    "Latvian", "Lithuanian", "Estonian", "Finnish", "Swedish", "Norwegian", "Danish", "Icelandic", "Czech",
    "Slovak", "Slovenian", "Croatian", "Serbian", "Bosnian", "Montenegrin", "Macedonian", "Bulgarian",
    "Romanian", "Moldovan", "Hungarian", "Albanian", "Maltese", "Irish", "Welsh", "Basque", "Catalan",
    "Galician", "Belarusian", "Georgian", "Armenian", "Azerbaijani", "Azeri", "Kazakh", "Uzbek", "Kyrgyz",
    "Tajik", "Turkmen", "Mongolian", "Pashto", "Dari", "Kurdish", "Amharic", "Somali", "Hausa", "Yoruba",
    "Igbo", "Zulu", "Xhosa", "Afrikaans", "Malagasy", "Lao", "Tibetan", "Hmong", "Javanese", "Sundanese",
    "Cebuano", "Haitian Creole", "Quechua", "Hebrew", "Taiwanese", "Hokkien", "Brazilian Portuguese",
]
LANGUAGE_ALIASES = {"bangla": "Bengali", "oriya": "Odia", "farsi": "Persian", "mandarin": "Chinese", "filipino": "Tagalog",
                    "azeri": "Azerbaijani", "brazilian portuguese": "Portuguese", "taiwanese": "Chinese", "hokkien": "Chinese",
                    "cantonese": "Chinese", "montenegrin": "Serbian", "moldovan": "Romanian"}

# Skills the user is NOT assumed to have; used to surface missing requirements.
OTHER_REQUIREMENTS: dict[str, list[str]] = {
    "Programming (Python/JS etc.)": [r"\bpython\b", r"javascript", r"typescript", r"\bjava\b", r"\bc\+\+", r"\bgolang\b", r"\brust\b", r"coding (experience|skills|expert)", r"software engineer\w*", r"competitive programming"],
    "SQL / data analysis": [r"\bsql\b", r"\btableau\b", r"power ?bi", r"data analyst", r"\bpandas\b"],
    "Machine learning engineering": [r"\bpytorch\b", r"tensorflow", r"model (fine[- ]tuning|deployment)", r"ml engineer"],
    "Medical / clinical credential": [r"\bmbbs\b", r"\bmd\b(?! format)", r"registered nurse", r"medical (degree|doctor)", r"physician"],
    "Law degree / bar": [r"\bllb\b", r"\bjd\b", r"law degree", r"bar admission", r"licensed attorney", r"practicing lawyer"],
    "Accounting credential": [r"\bcpa\b", r"chartered accountant", r"\bacca\b"],
    "Finance / FP&A expertise": [r"\bfp&a\b", r"financial (planning|modeling|modelling|analyst)", r"investment banking", r"\bcfa\b", r"quantitative finance"],
    "Voice acting / recording studio": [r"voice (actor|acting|over artist)", r"studio[- ]quality recording", r"home studio"],
    "Sales / quota": [r"\bquota\b", r"account executive", r"business development representative", r"cold calling"],
    "Native English (US/UK)": [r"native english speaker", r"native[- ]level english \(us\)", r"\b(us|uk) english native"],
    "Advanced degree (Master's/PhD)": [r"\bph\.?d\b", r"doctorate", r"master'?s (degree )?(required|in linguistics)"],
    "Mathematics / STEM expertise": [r"\bmathematic\w*\b", r"\bphysics\b", r"\bchemistry\b", r"stem (expert|degree|background)"],
    "Driving / field work": [r"driver'?s licen[cs]e", r"field (work|survey)", r"travel required"],
}

HARD_INELIGIBLE_PATTERNS: dict[str, str] = {
    "US citizenship / clearance required": r"(u\.?s\.? citizen(ship)? (is )?required|must be a u\.?s\.? citizen|security clearance|public trust clearance|only (open|available) to (us|u\.s\.) citizens)",
    "Must be authorised to work in US/EU/UK without sponsorship": r"must (be|have) (legally )?(authori[sz]ed|eligible|the right) to work in (the )?(us|u\.s\.|united states|uk|united kingdom|eu|european union|canada|australia)",
    "Must reside in a specific non-India country": r"(must|need to) (reside|live|be based|be located) in (the )?(us|u\.s\.|usa|united states|uk|canada|europe|eu|germany|france|spain|australia|brazil|mexico|japan|philippines)",
}


def _compile(patterns: list[str]) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in patterns), re.I)


@lru_cache(maxsize=1)
def compiled() -> dict[str, dict[str, re.Pattern]]:
    out: dict[str, dict[str, re.Pattern]] = {"skills": {}, "tools": {}, "domains": {}, "other": {}, "skill_group": {}}
    for group, items in SKILLS.items():
        for name, pats in items.items():
            out["skills"][name] = _compile(pats)
            out["skill_group"][name] = group  # type: ignore[assignment]
    for name, pats in TOOLS.items():
        out["tools"][name] = _compile(pats)
    for name, pats in DOMAINS.items():
        out["domains"][name] = _compile(pats)
    for name, pats in OTHER_REQUIREMENTS.items():
        out["other"][name] = _compile(pats)
    return out


def find(text: str, kind: str) -> list[str]:
    return [name for name, pat in compiled()[kind].items() if pat.search(text or "")]


def find_languages(text: str) -> list[str]:
    found = []
    for lang in LANGUAGES:
        if re.search(rf"\b{lang}\b", text or "", re.I):
            canon = LANGUAGE_ALIASES.get(lang.lower(), lang)
            if canon not in found:
                found.append(canon)
    return found


# Core signals: a job needs at least one of these to be considered in-scope
# for a Hindi language / AI data profile.
CORE_SKILLS = {
    name for group in ("language_services", "ai_data") for name in SKILLS[group]
} | {"Localization testing"}

# Category -> search keywords (used by the query generator and quick filters)
CATEGORY_QUERIES: dict[str, list[str]] = {
    "English-to-Hindi Translator": ["Hindi translator", "English Hindi translation", "Hindi translation"],
    "Hindi Localization Specialist": ["Hindi localization", "localization specialist Hindi"],
    "Hindi Linguistic QA / LQA Reviewer": ["Hindi LQA", "linguistic quality assurance Hindi", "Hindi linguist"],
    "Hindi Proofreader and Language Editor": ["Hindi proofreader", "Hindi editor"],
    "AI Language Evaluator": ["language evaluator", "AI evaluator Hindi"],
    "Hindi AI Trainer": ["Hindi AI trainer", "AI trainer Hindi", "Hindi language specialist AI"],
    "AI Data Annotator": ["data annotator Hindi", "annotation Hindi"],
    "Hindi Language Specialist": ["Hindi language specialist", "Hindi language expert"],
    "Multilingual Dataset Evaluator": ["multilingual data", "dataset evaluator"],
    "AI Response Evaluator / AI Model Rater": ["AI rater", "response evaluator", "model evaluator"],
    "Hindi Transcriptionist": ["Hindi transcription", "Hindi transcriber"],
    "Audio and Speech Data Evaluator": ["speech evaluation Hindi", "audio evaluator Hindi"],
    "Hindi Content Reviewer": ["Hindi content reviewer", "Hindi content moderator"],
    "Localization QA Tester": ["localization QA", "localization tester"],
    "Language Quality Lead": ["language quality lead", "language lead"],
    "Hindi Linguistic Project Coordinator": ["linguistic project coordinator", "localization project coordinator"],
    "Prompt Evaluator / Multilingual Red Teaming": ["red teaming multilingual", "prompt evaluator"],
    "NLP Data Quality Analyst": ["NLP data quality", "linguistic data analyst"],
    "AI Safety and Content Quality Evaluator": ["AI safety evaluator", "content quality evaluator"],
    "Translation Project Manager": ["translation project manager", "localization project manager"],
    # secondary
    "Search Quality Evaluation": ["search quality rater", "search evaluator India"],
    "Voice Assistant Evaluation": ["voice assistant evaluation Hindi"],
    "Localization Operations / Vendor Management": ["localization operations", "translation vendor manager"],
    "Hindi Technical / Content Writing": ["Hindi content writer", "Hindi technical writer"],
    "Editorial QA": ["editorial quality assurance", "Hindi sub editor"],
    "Dataset Quality Control": ["data quality analyst annotation"],
}

AI_CATEGORIES = {
    "AI Language Evaluator", "Hindi AI Trainer", "AI Data Annotator", "Multilingual Dataset Evaluator",
    "AI Response Evaluator / AI Model Rater", "Audio and Speech Data Evaluator", "Prompt Evaluator / Multilingual Red Teaming",
    "NLP Data Quality Analyst", "AI Safety and Content Quality Evaluator", "Search Quality Evaluation",
    "Voice Assistant Evaluation", "Dataset Quality Control",
}
LOCALIZATION_CATEGORIES = {
    "English-to-Hindi Translator", "Hindi Localization Specialist", "Hindi Linguistic QA / LQA Reviewer",
    "Hindi Proofreader and Language Editor", "Hindi Language Specialist", "Localization QA Tester",
    "Language Quality Lead", "Hindi Linguistic Project Coordinator", "Translation Project Manager",
    "Localization Operations / Vendor Management", "Hindi Content Reviewer", "Hindi Transcriptionist",
}
