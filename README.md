# Cold and Flu Checker: LLM + RAG vs LLM + RAG + Knowledge Graph

A side by side comparison of two AI assistants answering questions about common UK cold and flu remedies. One uses plain document search (RAG). The other also follows a knowledge graph linking products to ingredients, and ingredients to official warnings.

**Live demo:** https://cold-flu-graphrag.streamlit.app

> Educational demo only, not medical advice. Always read the product leaflet and speak to a pharmacist or doctor.

## Why I built this

Many cold and flu remedies share ingredients, so it is easy to double up on paracetamol, or take a decongestant you shouldn't with high blood pressure, without realising. Each product leaflet only describes its own product. The risk sits in the connections between them.

The project was inspired by Chuka Ofili's talk on grounding Gemini with knowledge graphs at DevFest Wales 2026. I am a pharmacy graduate studying for an MSc in Data Science, so I chose a problem I know from the pharmacy counter.

## How it works

**Knowledge graph (Neo4j AuraDB)**
* 8 products, 7 ingredients, 6 ingredient classes, 4 conditions, plus one withdrawn product (Day Nurse Capsules, MHRA recall, March 2023)
* Relationships include `CONTAINS`, `BELONGS_TO`, `CONTRAINDICATED_IN`, `CAUTION_IN`, `CAUSES`, `AFFECTS` and `AVOID_WITH`
* Every relationship carries its **source** and a **confidence level** (high, medium or low), taken from official product information

**Two pipelines, same model, same documents**
* **LLM + RAG:** Gemini answers from the 3 product documents closest to the question, found by embedding search
* **LLM + RAG + KG:** the same 3 documents, plus six graph query tools Gemini can call through function calling

The app also draws the exact graph connections behind each answer, built from what the graph actually returned.

## What I found

I tested both pipelines on 9 fixed questions, using the same model (gemini-3.1-flash-lite) and the same retrieved documents.

| Question | LLM + RAG | LLM + RAG + KG |
|---|---|---|
| Lemsip Max with Beechams? | Correct | Correct |
| Already had paracetamol, what to avoid? | Missed Beechams | Complete |
| High blood pressure, what to avoid? | Found 3 of 6 | Found 6 of 6 |
| Which products cause drowsiness? | Incomplete | Complete |
| Driving after Night Nurse? | Correct | Correct |
| Can a 14 year old take Night Nurse? | Correct | Correct |
| Lemsip Max vs standard Lemsip? | Correct | Correct |
| Nurofen Cold & Flu with Sudafed? | Understated the risk | Flagged the double decongestant |
| What's in Day Nurse Capsules? | Withdrawn, no ingredients | Withdrawn, with ingredients |

**In short:** on questions about one or two products, plain RAG did just as well. The graph made the difference on questions that cut across several products, because RAG can only read a few documents at a time.

## Limitations

* Small scale: 8 products, chosen to demonstrate the approach
* RAG retrieves 3 documents. With only 9 documents, retrieving more would help RAG, but real catalogues hold thousands, so a limit always exists
* Product names are matched by text, so unusual names may not be found. Node embeddings would be the next improvement
* The graph does not track doses already taken or timing between doses

## Data sources

Official product information from the [emc](https://www.medicines.org.uk/emc), the NHS medicines pages and the MHRA. Product summaries are written in my own words from these sources.

## Tech stack

Neo4j AuraDB, Cypher, Python, Gemini API (google-genai), Gemini embeddings, Streamlit

## Run it locally

1. `pip install -r requirements.txt`
2. Create `.streamlit/secrets.toml` with `GEMINI_API_KEY`, `NEO4J_URI`, `NEO4J_USERNAME` and `NEO4J_PASSWORD`
3. Load the graph into your Neo4j instance using `graph_setup.cypher`
4. `streamlit run app.py`

## Author

Maureen Okoro, pharmacy graduate and MSc Data Science student at the University of South Wales.
[LinkedIn](https://www.linkedin.com/in/maureenokoro-analytics) 
