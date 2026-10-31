import html
import re
import time
import numpy as np
import streamlit as st
import streamlit.components.v1 as components
from neo4j import GraphDatabase
from google import genai
from google.genai import types

st.set_page_config(page_title="Cold and Flu Checker", page_icon="💊", layout="wide")

MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash"]
EMBED_MODELS = ["gemini-embedding-001", "text-embedding-004"]
MAX_QUESTIONS = 10
SHOW_REASONS = True

RED, AMBER, GREY = "#A32D2D", "#B26B00", "#9AA5A0"

# ---------- Look and feel ----------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stMarkdownContainer"],
button, textarea, input { font-family: 'Atkinson Hyperlegible', sans-serif !important; }
#MainMenu, footer { visibility: hidden; }
.block-container { max-width: 1000px; padding-top: 2.2rem; padding-bottom: 7rem; }

.hero h1 { font-size: 2.1rem; font-weight: 700; color: #1C2420; margin: 0 0 .3rem; line-height: 1.15; }
.hero p { font-size: 1.05rem; color: #5B6770; margin: 0 0 .6rem; line-height: 1.6; max-width: 65ch; }
.start { color: #5B6770; font-size: .95rem; margin: 2.2rem 0 .5rem; }

button[data-testid="stBaseButton-secondary"] { width: 100%; border-radius: 4px; border: 1px solid #CBD5D0;
  background: #FFFFFF; color: #1C2420; font-weight: 400; white-space: normal; padding: .5rem .8rem; }
button[data-testid="stBaseButton-secondary"]:hover { border-color: #0E6B4F; color: #0E6B4F; }
button:focus-visible { outline: 3px solid #B26B00 !important; outline-offset: 2px; }

.bubble-row { display: flex; justify-content: flex-end; margin: 1.8rem 0 1.3rem; }
.bubble { background: #E9EEEB; border-radius: 16px; padding: .65rem 1rem; max-width: 75%;
  font-size: 1rem; line-height: 1.5; color: #1C2420; }

.colhead { font-weight: 700; font-size: 1rem; color: #1C2420; margin: 0; }
.colhead.kg { color: #0E6B4F; }
.colsub { font-size: .85rem; color: #7A857F; margin: 0 0 .9rem; }
.ans { margin: 0 0 .5rem; line-height: 1.55; font-size: .98rem; color: #1C2420; }
.ans .pn { font-weight: 700; }
.v.avoid { color: #B3261E; font-weight: 700; }
.v.caution { color: #8A5300; font-weight: 700; }
.v.ok { color: #0E6B4F; font-weight: 700; }
.ans .why { color: #5B6770; }
.missing { color: #7A857F; font-style: italic; margin-top: .6rem; font-size: .92rem; }
.readhead { font-size: .8rem; color: #7A857F; margin: 1.2rem 0 .35rem; }
.readitem { font-size: .88rem; color: #5B6770; border-left: 2px solid #CBD5D0; padding-left: .5rem; margin: 0 0 .3rem; }

.graphhead { font-weight: 700; font-size: 1rem; color: #1C2420; margin: 1.6rem 0 .1rem;
  padding-top: 1.1rem; border-top: 1px solid #E1E6E3; }
.graphsub { font-size: .9rem; color: #5B6770; margin: 0 0 .3rem; }
.legend { display: flex; flex-wrap: wrap; gap: 1.1rem; font-size: .82rem; color: #5B6770; margin: .1rem 0 .5rem; }
.legend span { display: inline-flex; align-items: center; gap: .4rem; }
.meta { font-size: .8rem; color: #9AA5A0; margin: .5rem 0 0; }
.turn-end { border: none; border-top: 1px solid #E1E6E3; margin: 2rem 0 0; }
</style>
""", unsafe_allow_html=True)

# ---------- Connections (keys come from Streamlit secrets, never from the code) ----------

@st.cache_resource
def get_driver():
    d = GraphDatabase.driver(st.secrets["NEO4J_URI"],
                             auth=(st.secrets["NEO4J_USERNAME"], st.secrets["NEO4J_PASSWORD"]))
    d.verify_connectivity()
    return d

@st.cache_resource
def get_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"],
                        http_options=types.HttpOptions(timeout=60000))

driver = get_driver()
client = get_client()

# ---------- Knowledge graph tools ----------

PRODUCT_QUERY = """
MATCH (p) WHERE (p:Product OR p:WithdrawnProduct) AND toLower(p.name) CONTAINS toLower($name)
OPTIONAL MATCH (p)-[c:CONTAINS|CONTAINED]->(i:Ingredient)
OPTIONAL MATCH (i)-[:BELONGS_TO]->(cl:IngredientClass)
OPTIONAL MATCH (i)-[w:CONTRAINDICATED_IN|CAUTION_IN]->(cond:Condition)
OPTIONAL MATCH (i)-[e:CAUSES]->(eff:Effect)
OPTIONAL MATCH (eff)-[a:AFFECTS]->(act:Activity)
OPTIONAL MATCH (i)-[x:INTERACTS_WITH]-(other)
WITH p, i, c, cl,
  collect(DISTINCT CASE WHEN cond IS NOT NULL THEN {condition: cond.name, type: type(w), detail: w.detail,
    confidence: w.confidence, verified: w.verified, source: w.source} END) AS warnings,
  collect(DISTINCT CASE WHEN eff IS NOT NULL THEN {effect: eff.name, confidence: e.confidence,
    affects: act.name, affects_detail: a.detail, affects_confidence: a.confidence} END) AS effects,
  collect(DISTINCT CASE WHEN other IS NOT NULL THEN {with: other.name, effect: x.effect,
    severity: x.severity, confidence: x.confidence} END) AS interactions
RETURN p.name AS product, labels(p)[0] AS kind, properties(p) AS details,
  collect(CASE WHEN i IS NOT NULL THEN {ingredient: i.name, amount: c.amount, class: cl.name,
    confidence: c.confidence, warnings: warnings, effects: effects, interactions: interactions} END) AS ingredients
"""

PRODUCT_WARNINGS_QUERY = """
MATCH (p:Product {name: $name})-[r:CONTRAINDICATED_IN|CAUTION_IN|AFFECTS]->(x)
RETURN type(r) AS link, x.name AS target, r.detail AS detail,
  r.confidence AS confidence, r.verified AS verified, r.source AS source
"""

def get_product_info(product_name: str) -> list:
    """Look up a cold and flu product in the knowledge graph, including withdrawn products.
    Returns its ingredients and amounts, ingredient classes, warnings for conditions,
    effects such as drowsiness, interactions, warnings stated on the product leaflet itself,
    minimum age and maximum daily doses, each with its source and confidence level.
    Products: Lemsip Max Cold & Flu Lemon, Lemsip Cold & Flu Lemon, Beechams All in One,
    Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant Tablets, Paracetamol 500mg Tablets,
    Ibuprofen 200mg Tablets. Withdrawn: Day Nurse Capsules."""
    records, _, _ = driver.execute_query(PRODUCT_QUERY, parameters_={"name": product_name})
    if not records:
        return [{"found": False, "message": f"{product_name} is not in the knowledge graph."}]
    results = []
    for r in records:
        data = r.data()
        warnings, _, _ = driver.execute_query(PRODUCT_WARNINGS_QUERY, parameters_={"name": data["product"]})
        data["product_level_warnings"] = [w.data() for w in warnings]
        results.append(data)
    return results

COMBINATION_QUERY = """
MATCH (a:Product) WHERE toLower(a.name) CONTAINS toLower($a)
MATCH (b:Product) WHERE toLower(b.name) CONTAINS toLower($b) AND a <> b
OPTIONAL MATCH (a)-[:CONTAINS]->(shared:Ingredient)<-[:CONTAINS]-(b)
OPTIONAL MATCH (a)-[:CONTAINS]->(ia:Ingredient)-[:BELONGS_TO]->(cls:IngredientClass)<-[:BELONGS_TO]-(ib:Ingredient)<-[:CONTAINS]-(b)
  WHERE ia <> ib
OPTIONAL MATCH (a)-[:CONTAINS]->(xa:Ingredient)-[x:INTERACTS_WITH]-(xb:Ingredient)<-[:CONTAINS]-(b)
OPTIONAL MATCH (a)-[av1:AVOID_WITH]->(:IngredientClass)<-[:BELONGS_TO]-(ci1:Ingredient)<-[:CONTAINS]-(b)
OPTIONAL MATCH (b)-[av2:AVOID_WITH]->(:IngredientClass)<-[:BELONGS_TO]-(ci2:Ingredient)<-[:CONTAINS]-(a)
RETURN a.name AS product_a, b.name AS product_b,
  a.label_warning AS label_warning_a, b.label_warning AS label_warning_b,
  a.dose_note AS dose_note_a, b.dose_note AS dose_note_b,
  collect(DISTINCT shared.name) AS shared_ingredients,
  collect(DISTINCT CASE WHEN cls IS NOT NULL THEN cls.name + ' (' + ia.name + ' and ' + ib.name + ')' END) AS same_class_different_ingredients,
  collect(DISTINCT CASE WHEN x IS NOT NULL THEN {between: xa.name + ' and ' + xb.name, effect: x.effect,
    severity: x.severity, confidence: x.confidence} END) AS interactions,
  collect(DISTINCT CASE WHEN av1 IS NOT NULL THEN a.name + ': ' + av1.detail + ' (' + b.name + ' contains ' + ci1.name + ', confidence: ' + av1.confidence + ')' END)
  + collect(DISTINCT CASE WHEN av2 IS NOT NULL THEN b.name + ': ' + av2.detail + ' (' + a.name + ' contains ' + ci2.name + ', confidence: ' + av2.confidence + ')' END) AS leaflet_warnings
"""

def check_combination(product_a: str, product_b: str) -> list:
    """Check whether two products can be taken together. Returns ingredients they share
    (risk of a double dose), different ingredients from the same class (for example two
    decongestants), known ingredient interactions, leaflet warnings and dose notes,
    with confidence levels."""
    records, _, _ = driver.execute_query(COMBINATION_QUERY, parameters_={"a": product_a, "b": product_b})
    if not records:
        return [{"found": False, "message": "One or both products are not in the knowledge graph."}]
    return [r.data() for r in records]

CONDITION_QUERY = """
MATCH (p:Product)-[:CONTAINS]->(i:Ingredient)-[r:CONTRAINDICATED_IN|CAUTION_IN]->(c:Condition)
WHERE toLower(c.name) CONTAINS toLower($condition)
RETURN p.name AS product, c.name AS condition, 'ingredient: ' + i.name AS via, type(r) AS link,
  r.detail + CASE WHEN r.confidence <> 'high' THEN ' (not fully confirmed, check the leaflet)' ELSE '' END AS detail,
  r.confidence AS confidence, r.verified AS verified, r.source AS source
UNION
MATCH (p:Product)-[r:CONTRAINDICATED_IN|CAUTION_IN]->(c:Condition)
WHERE toLower(c.name) CONTAINS toLower($condition)
RETURN p.name AS product, c.name AS condition, 'product leaflet' AS via, type(r) AS link,
  r.detail + CASE WHEN r.confidence <> 'high' THEN ' (not fully confirmed, check the leaflet)' ELSE '' END AS detail,
  r.confidence AS confidence, r.verified AS verified, r.source AS source
"""

def find_products_to_avoid(condition: str) -> list:
    """Find every product that is contraindicated or needs caution for a condition,
    either through an ingredient it contains or stated on its own leaflet.
    Conditions in the graph: 'High Blood Pressure' (also called hypertension),
    'Liver Disease', 'Asthma', 'Pregnancy'."""
    records, _, _ = driver.execute_query(CONDITION_QUERY, parameters_={"condition": condition})
    return [r.data() for r in records]

INGREDIENT_QUERY = """
MATCH (p:Product)-[c:CONTAINS]->(i:Ingredient) WHERE toLower(i.name) = toLower($ingredient)
RETURN p.name AS product, i.name AS ingredient, c.amount AS amount, c.confidence AS confidence,
  p.max_daily_doses AS max_daily_doses, p.dose_note AS dose_note
"""

def find_products_containing(ingredient: str) -> list:
    """Find every product that contains an ingredient, for example to avoid a double dose.
    Ingredients: Paracetamol, Ibuprofen, Phenylephrine, Pseudoephedrine, Guaifenesin,
    Dextromethorphan, Promethazine."""
    records, _, _ = driver.execute_query(INGREDIENT_QUERY, parameters_={"ingredient": ingredient})
    return [r.data() for r in records]

EFFECT_QUERY = """
MATCH (p:Product)-[:CONTAINS]->(i:Ingredient)-[e:CAUSES]->(eff:Effect)
WHERE toLower(eff.name) = toLower($effect)
OPTIONAL MATCH (eff)-[a:AFFECTS]->(act:Activity)
OPTIONAL MATCH (i)-[x:INTERACTS_WITH]->(s:Substance)
RETURN p.name AS product, i.name AS ingredient, eff.name AS effect, e.confidence AS confidence,
  collect(DISTINCT act.name + ': ' + a.detail + ' (confidence: ' + a.confidence + ')') AS affects,
  collect(DISTINCT s.name + ': ' + x.effect) AS avoid_with
"""

def find_products_with_effect(effect: str) -> list:
    """Find every product that can cause an effect, and what it affects (for example driving)
    or should not be combined with (for example alcohol). Effects in the graph: 'Drowsiness'."""
    records, _, _ = driver.execute_query(EFFECT_QUERY, parameters_={"effect": effect})
    return [r.data() for r in records]

DRIVING_QUERY = """
MATCH (p:Product)-[:CONTAINS]->(i:Ingredient)-[:CAUSES]->(eff:Effect)-[a:AFFECTS]->(:Activity {name: 'Driving'})
RETURN p.name AS product, 'ingredient ' + i.name + ' causes ' + eff.name AS reason,
  a.detail AS detail, a.confidence AS confidence, a.source AS source
UNION
MATCH (p:Product)-[a:AFFECTS]->(:Activity {name: 'Driving'})
RETURN p.name AS product, 'stated on product leaflet' AS reason,
  a.detail AS detail, a.confidence AS confidence, a.source AS source
"""

def find_driving_warnings() -> list:
    """Find every product that may affect the ability to drive, either because an
    ingredient causes drowsiness or because the product leaflet says so."""
    records, _, _ = driver.execute_query(DRIVING_QUERY)
    return [r.data() for r in records]

GRAPH_TOOLS = [get_product_info, check_combination, find_products_to_avoid,
               find_products_containing, find_products_with_effect, find_driving_warnings]

# ---------- Documents for retrieval (written from official emc, NHS and MHRA texts) ----------

DOCUMENTS = [
  {"title": "Lemsip Max Cold & Flu Lemon", "source": "https://www.medicines.org.uk/emc/product/5612/smpc",
   "text": "Hot drink powder for short term relief of cold and flu symptoms. Each sachet contains paracetamol 1000mg and phenylephrine hydrochloride 12.2mg. For adults and children aged 16 and over; not for under 16s. One sachet every 4 to 6 hours, no more than 4 sachets in 24 hours, for up to 5 days. Do not take with other medicines containing paracetamol. Because of the phenylephrine, it must not be used by people with hypertension, severe heart disease or cardiovascular disorders, hyperthyroidism, diabetes, an enlarged prostate, closed angle glaucoma or phaeochromocytoma, by people taking MAOIs or within two weeks of stopping them, or with other decongestants. Avoid in pregnancy unless a healthcare professional recommends it. It has no or negligible effect on driving."},
  {"title": "Lemsip Cold & Flu Lemon", "source": "https://www.medicines.org.uk/emc/product/617/smpc",
   "text": "Hot drink powder for short term relief of cold and flu symptoms. Each sachet contains paracetamol 650mg and phenylephrine hydrochloride 10mg. For adults and children aged 16 and over; not for under 16s. One sachet every 4 to 6 hours, no more than 4 sachets in 24 hours, for up to 5 days. Do not take with other medicines containing paracetamol. Because of the phenylephrine, it must not be used by people with hypertension, severe heart disease or cardiovascular disorders, hyperthyroidism, diabetes, an enlarged prostate, closed angle glaucoma or phaeochromocytoma, by people taking MAOIs or within two weeks of stopping them, or with other decongestants. Avoid in pregnancy unless a healthcare professional recommends it. It has no or negligible effect on driving."},
  {"title": "Beechams All in One", "source": "https://www.medicines.org.uk/emc/product/3913/smpc",
   "text": "Oral solution for colds, chills and flu with chesty coughs. Each 20ml dose contains paracetamol 500mg, guaifenesin 200mg and phenylephrine hydrochloride 10mg. For adults and children aged 16 and over: one dose every 4 hours, no more than four doses in 24 hours, and not for more than 5 days without medical advice. Must not be used with other decongestants, or by people with hypertension, heart disease, diabetes, hyperthyroidism, liver impairment or severe kidney impairment, an enlarged prostate, closed angle glaucoma or phaeochromocytoma, or by people taking tricyclic antidepressants, beta blockers or MAOIs. Seek medical advice first with a chronic cough such as with asthma. Do not take with a cough suppressant, other cough and cold medicines, or alcohol. It contains 19% alcohol; do not drive if affected by dizziness. Its ingredients are not known to cause sedation. Not for use in pregnancy or breastfeeding without medical advice."},
  {"title": "Night Nurse", "source": "https://www.medicines.org.uk/emc/product/354/smpc",
   "text": "Oral solution for night time relief of colds, chills and flu. Each 20ml dose contains paracetamol 1000mg, promethazine hydrochloride 20mg and dextromethorphan hydrobromide 15mg. For adults and children aged 16 and over; not for under 16s except on medical advice. Take one dose at bedtime, only once per night, for no more than 3 days. Other paracetamol products may be taken during the day if total paracetamol stays within 4000mg in 24 hours, with at least 4 hours between doses. Do not use with other cough or cold medicines or any antihistamine products. Must not be used during an asthma attack or flare up, by people at risk of respiratory failure, or with MAOIs. Seek medical advice first with liver or kidney impairment, chronic cough, glaucoma, cardiovascular problems, prostate problems or epilepsy. Promethazine very commonly causes drowsiness, and alcohol increases this. It can seriously affect driving and is covered by drug driving law under section 5a of the Road Traffic Act 1988. Not for use in pregnancy or breastfeeding without medical advice."},
  {"title": "Nurofen Cold & Flu", "source": "https://www.medicines.org.uk/emc/product/5626/smpc",
   "text": "Tablets for cold and flu with congestion. Each tablet contains ibuprofen 200mg and pseudoephedrine hydrochloride 30mg. Adults and children over 12: one or two tablets up to three times a day, at least 4 hours apart, no more than 6 tablets in 24 hours. Not for children under 12. Must not be used with severe or uncontrolled hypertension, severe heart disease, a history of stomach ulcers or bleeding, in the last three months of pregnancy, or with MAOIs. Speak to a pharmacist or doctor first with asthma or high blood pressure. Do not take with other NSAIDs, and take care with other decongestants. No effect on driving is expected."},
  {"title": "Sudafed Decongestant Tablets", "source": "https://www.medicines.org.uk/emc/product/6199/smpc",
   "text": "Non drowsy tablets for blocked nose and sinuses. Each tablet contains pseudoephedrine hydrochloride 60mg. Must not be used by people with cardiovascular disease including hypertension. Take care with moderate kidney impairment or severe liver impairment."},
  {"title": "Paracetamol 500mg Tablets", "source": "https://www.nhs.uk/medicines/paracetamol-for-adults/",
   "text": "Painkiller for pain and high temperature. Adults take one or two tablets up to 4 times in 24 hours, no more than 8 tablets in 24 hours. Do not take with other medicines containing paracetamol, such as cold and flu remedies, because of the risk of overdose. Liver disease increases the risk of liver damage."},
  {"title": "Ibuprofen 200mg Tablets", "source": "https://www.nhs.uk/medicines/ibuprofen-for-adults/",
   "text": "An NSAID painkiller for pain, inflammation and fever. Indigestion and stomach aches are common side effects. It can be taken with paracetamol, but not with other NSAIDs."},
  {"title": "MHRA pholcodine withdrawal, March 2023", "source": "https://www.gov.uk/drug-device-alerts/class-2-medicines-recall-various-marketing-authorisation-holders-pholcodine-containing-products-el-23-a-slash-09",
   "text": "All medicines containing pholcodine were withdrawn from the UK market as a precaution in March 2023, including Day Nurse Capsules, Day & Night Nurse Capsules and Day Nurse."},
]

@st.cache_resource
def build_index():
    texts = [d["title"] + ". " + d["text"] for d in DOCUMENTS]
    for model in EMBED_MODELS:
        try:
            res = client.models.embed_content(model=model, contents=texts,
                                              config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"))
            vecs = np.array([e.values for e in res.embeddings])
            return model, vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
        except Exception:
            continue
    raise RuntimeError("No embedding model is available right now.")

def retrieve(question, k=3):
    model, doc_vectors = build_index()
    res = client.models.embed_content(model=model, contents=[question],
                                      config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"))
    q = np.array(res.embeddings[0].values)
    q = q / np.linalg.norm(q)
    top = np.argsort(doc_vectors @ q)[::-1][:k]
    return [DOCUMENTS[i] for i in top]

# ---------- The two pipelines ----------

STYLE = (
    "Answer directly and concisely in plain English. Never mention 'the documents', 'the knowledge graph', "
    "'the tools' or 'the leaflet'; just state the facts. When the answer covers products, write one bullet "
    "per product in exactly this format: '- **Product name**: Avoid. Short reason.' The verdict must be "
    "Avoid, Caution or OK. Never group several products in one bullet, and do not add an introduction or "
    "summary paragraph around the list. For a question about a single fact, "
    "answer in one or two short sentences instead. Do not add a medical disclaimer, because the page shows one."
)

RAG_CONFIG = types.GenerateContentConfig(system_instruction=(
    "You answer questions about UK cold and flu products using only the information provided. "
    "If information about a product the user asked about is missing, end with one line: "
    "'No information found for: ...' " + STYLE))

RAG_KG_CONFIG = types.GenerateContentConfig(tools=GRAPH_TOOLS, system_instruction=(
    "You answer questions about UK cold and flu products. Always use the graph tools, and also use the "
    "information provided. If they conflict, follow the more cautious official wording. "
    "If neither has the answer, say so. If a product is withdrawn, say so clearly. "
    "If products share paracetamol, follow each product's own instruction: most say do not take with other "
    "paracetamol products; only apply dose spacing where a product's own dose_note allows it. "
    "Keep '(not fully confirmed, check the leaflet)' wherever it appears in the data, and add it after any "
    "other medium or low confidence fact. " + STYLE))

def generate(contents, cfg):
    last_error = "unknown error"
    for model in MODELS:
        for attempt in range(2):
            try:
                return client.models.generate_content(model=model, contents=contents, config=cfg), model
            except Exception as e:
                last_error = str(e)[:120]
                time.sleep(3)
    return None, last_error

def format_docs(docs):
    return "\n\n".join(f"[{d['title']}] {d['text']}" for d in docs)

# ---------- Reading what the graph actually returned ----------

def graph_activity(resp):
    """Pair each graph tool call with its result, in order."""
    calls, results, pending = [], [], {}
    try:
        for content in resp.automatic_function_calling_history or []:
            for part in content.parts or []:
                fc = getattr(part, "function_call", None)
                if fc:
                    args = dict(fc.args or {})
                    pending.setdefault(fc.name, []).append(args)
                    calls.append(f"{fc.name}(" + ", ".join(f"{k}={v!r}" for k, v in args.items()) + ")")
                fr = getattr(part, "function_response", None)
                if fr:
                    args = pending.get(fr.name, [{}]).pop(0) if pending.get(fr.name) else {}
                    data = fr.response
                    if isinstance(data, dict) and "result" in data:
                        data = data["result"]
                    results.append((fr.name, args, data if isinstance(data, list) else [data]))
    except Exception:
        pass
    return calls, results

def build_edges(results):
    edges = {}

    def add(src, scol, dst, dcol, kind, detail="", conf="high", source=""):
        if not src or not dst:
            return
        key = (src, dst, kind)
        if key not in edges:
            edges[key] = {"src": src, "scol": scol, "dst": dst, "dcol": dcol, "kind": kind,
                          "detail": detail or "", "conf": conf or "high", "source": source or ""}

    for name, args, rows in results:
        for r in rows:
            if not isinstance(r, dict) or r.get("found") is False:
                continue
            if name == "find_products_to_avoid":
                via = r.get("via") or ""
                if via.startswith("ingredient: "):
                    ing = via[len("ingredient: "):]
                    add(r.get("product"), 0, ing, 1, "CONTAINS")
                    add(ing, 1, r.get("condition"), 2, r.get("link"), r.get("detail"), r.get("confidence"), r.get("source"))
                else:
                    add(r.get("product"), 0, r.get("condition"), 2, r.get("link"), r.get("detail"),
                        r.get("confidence"), r.get("source"))
            elif name == "find_products_containing":
                add(r.get("product"), 0, r.get("ingredient"), 1, "CONTAINS", r.get("amount"), r.get("confidence"))
            elif name == "find_products_with_effect":
                add(r.get("product"), 0, r.get("ingredient"), 1, "CONTAINS")
                add(r.get("ingredient"), 1, r.get("effect"), 2, "CAUSES", "", r.get("confidence"))
            elif name == "find_driving_warnings":
                m = re.match(r"ingredient (.+) causes (.+)", r.get("reason") or "")
                if m:
                    add(r.get("product"), 0, m.group(1), 1, "CONTAINS")
                    add(m.group(1), 1, "Driving", 2, "AFFECTS", r.get("detail"), r.get("confidence"), r.get("source"))
                else:
                    add(r.get("product"), 0, "Driving", 2, "AFFECTS", r.get("detail"), r.get("confidence"), r.get("source"))
            elif name == "check_combination":
                a, b = r.get("product_a"), r.get("product_b")
                for s in r.get("shared_ingredients") or []:
                    add(a, 0, s, 1, "SHARED", f"Both products contain {s}")
                    add(b, 0, s, 1, "SHARED", f"Both products contain {s}")
                for item in r.get("same_class_different_ingredients") or []:
                    m = re.match(r"^(.+) \((.+) and (.+)\)$", item or "")
                    if m:
                        cls, ia, ib = m.groups()
                        add(a, 0, ia, 1, "CONTAINS")
                        add(b, 0, ib, 1, "CONTAINS")
                        add(ia, 1, cls, 2, "SAME_CLASS", f"Two {cls.lower()} ingredients together")
                        add(ib, 1, cls, 2, "SAME_CLASS", f"Two {cls.lower()} ingredients together")
                for item in r.get("leaflet_warnings") or []:
                    m = re.match(r"^(.+?): (.+) \((.+?) contains (.+?), confidence: (\w+)\)$", item or "")
                    if m:
                        prod, rule, other, ing, conf = m.groups()
                        add(other, 0, ing, 1, "CONTAINS")
                        add(prod, 0, ing, 1, "AVOID_WITH", rule, conf)
            elif name == "get_product_info":
                label = r.get("product")
                if r.get("kind") == "WithdrawnProduct":
                    label = f"{label} (withdrawn)"
                for ing in r.get("ingredients") or []:
                    if isinstance(ing, dict) and ing.get("ingredient"):
                        add(label, 0, ing["ingredient"], 1, "CONTAINS", ing.get("amount"), ing.get("confidence"))
                for w in r.get("product_level_warnings") or []:
                    add(label, 0, w.get("target"), 2, w.get("link"), w.get("detail"), w.get("confidence"), w.get("source"))
    return list(edges.values())

def edge_colour(kind):
    kind = (kind or "").upper()
    if kind in ("CONTAINS", "SHARED"):
        return GREY
    if kind in ("CONTRAINDICATED_IN", "AVOID_WITH", "SAME_CLASS"):
        return RED
    return AMBER

def short(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"

def graph_html(edges):
    cols = {0: [], 1: [], 2: []}
    for e in edges:
        if e["src"] not in cols[e["scol"]]:
            cols[e["scol"]].append(e["src"])
        if e["dst"] not in cols[e["dcol"]]:
            cols[e["dcol"]].append(e["dst"])
    shared = {e["dst"] for e in edges if (e["kind"] or "").upper() == "SHARED"}
    row = 30
    n = max(len(c) for c in cols.values()) or 1
    height = n * row + 20

    def ypos(col, name):
        items = cols[col]
        offset = (n - len(items)) * row / 2
        return 14 + offset + items.index(name) * row + 6

    out_x = {0: 215, 1: 470}
    in_x = {1: 322, 2: 548}
    parts = []
    for i, e in enumerate(edges):
        x1, y1 = out_x[e["scol"]], ypos(e["scol"], e["src"])
        x2, y2 = in_x[e["dcol"]], ypos(e["dcol"], e["dst"])
        mid = (x1 + x2) / 2
        d = f"M{x1} {y1} C{mid} {y1} {mid} {y2} {x2} {y2}"
        colour = edge_colour(e["kind"])
        dashed = e["conf"] != "high"
        tip = f"{e['src']} to {e['dst']}"
        if e["detail"]:
            tip += f": {e['detail']}"
        tip += f" ({e['conf']} confidence)"
        if e["source"]:
            tip += f". Source: {e['source']}"
        cls = "fade" if dashed else "draw"
        dash = ' stroke-dasharray="5 4"' if dashed else ""
        parts.append(
            f'<g><title>{html.escape(tip)}</title>'
            f'<path d="{d}" stroke="transparent" stroke-width="12" fill="none"/>'
            f'<path d="{d}" stroke="{colour}" stroke-width="1.8" fill="none"{dash} class="{cls}" '
            f'style="animation-delay:{i * 0.12:.2f}s"/></g>')

    for name in cols[0]:
        y = ypos(0, name)
        parts.append(f'<text x="0" y="{y + 4}" class="n0"><title>{html.escape(name)}</title>'
                     f'{html.escape(short(name, 29))}</text>')
    for name in cols[1]:
        y = ypos(1, name)
        if name in shared:
            parts.append(f'<circle cx="322" cy="{y}" r="5" fill="{RED}"/>'
                         f'<text x="333" y="{y + 4}" class="n1s">{html.escape(short(name, 20))}'
                         f'<tspan class="note"> shared</tspan></text>')
        else:
            parts.append(f'<circle cx="322" cy="{y}" r="3.5" fill="#5B6770"/>'
                         f'<text x="331" y="{y + 4}" class="n1">{html.escape(short(name, 20))}</text>')
    for name in cols[2]:
        y = ypos(2, name)
        parts.append(f'<circle cx="555" cy="{y}" r="7" fill="#FFFFFF" stroke="{RED}" stroke-width="1.8"/>'
                     f'<text x="568" y="{y + 4}" class="n2">{html.escape(short(name, 16))}</text>')

    return f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
body {{ margin: 0; font-family: 'Atkinson Hyperlegible', sans-serif; }}
.n0 {{ font-size: 13px; fill: #1C2420; font-weight: 700; }}
.n1 {{ font-size: 13px; fill: #5B6770; }}
.n1s {{ font-size: 13px; fill: #A32D2D; font-weight: 700; }}
.note {{ font-weight: 400; font-style: italic; fill: #A32D2D; }}
.n2 {{ font-size: 13px; fill: #A32D2D; font-weight: 700; }}
.draw {{ stroke-dasharray: 600; stroke-dashoffset: 600; animation: draw .9s ease-out forwards; }}
.fade {{ opacity: 0; animation: fade .6s ease-out forwards; }}
@keyframes draw {{ to {{ stroke-dashoffset: 0; }} }}
@keyframes fade {{ to {{ opacity: 1; }} }}
@media (prefers-reduced-motion: reduce) {{ .draw {{ animation: none; stroke-dashoffset: 0; }} .fade {{ animation: none; opacity: 1; }} }}
</style>
<svg width="100%" viewBox="0 0 680 {height}" role="img" aria-label="Connections the knowledge graph followed">
{''.join(parts)}
</svg>""", height

LEGEND = f"""
<div class="legend">
  <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="{GREY}" stroke-width="2"/></svg>Contains</span>
  <span><svg width="10" height="10"><circle cx="5" cy="5" r="4.5" fill="{RED}"/></svg>Ingredient shared by several products</span>
  <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="{RED}" stroke-width="2"/></svg>Avoid</span>
  <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="{AMBER}" stroke-width="2"/></svg>Caution</span>
  <span><svg width="22" height="6"><line x1="0" y1="3" x2="22" y2="3" stroke="{AMBER}" stroke-width="2" stroke-dasharray="4 3"/></svg>Not fully confirmed</span>
</div>"""

# ---------- Rendering ----------

VERDICT_LINE = re.compile(r"^\s*[\*\-]\s+\*\*(.+?)\*\*\s*:?\s*(Avoid|Caution|OK)\b[\.:]?\s*(.*)$", re.IGNORECASE)

def render_answer(text):
    out = []
    for line in text.splitlines():
        m = VERDICT_LINE.match(line)
        if m:
            product = html.escape(m.group(1).strip().rstrip(":"))
            word = m.group(2).lower()
            verdict = "OK" if word == "ok" else word
            reason = html.escape(m.group(3).replace("**", "").strip())
            why = f' <span class="why">{reason}</span>' if SHOW_REASONS and reason else ""
            out += ["", f'<p class="ans"><span class="pn">{product}</span>: '
                        f'<span class="v {word}">{verdict}</span>.{why}</p>', ""]
        elif line.strip().lower().startswith("no information found"):
            out += ["", f'<p class="missing">{html.escape(line.strip())}</p>', ""]
        else:
            out.append(line)
    st.markdown("\n".join(out), unsafe_allow_html=True)

def run_comparison(question):
    docs = retrieve(question)
    prompt = f"Information:\n{format_docs(docs)}\n\nQuestion: {question}"
    rag_resp, rag_model = generate(prompt, RAG_CONFIG)
    kg_resp, kg_model = generate(prompt, RAG_KG_CONFIG)
    calls, results = graph_activity(kg_resp) if kg_resp else ([], [])
    return {
        "question": question, "docs": docs,
        "rag_text": rag_resp.text if rag_resp else None, "rag_model": rag_model,
        "kg_text": kg_resp.text if kg_resp else None, "kg_model": kg_model,
        "calls": calls, "edges": build_edges(results),
    }

def render_turn(t):
    st.markdown(f'<div class="bubble-row"><div class="bubble">{html.escape(t["question"])}</div></div>',
                unsafe_allow_html=True)
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown('<p class="colhead">LLM + RAG</p><p class="colsub">Searches the leaflets</p>',
                    unsafe_allow_html=True)
        if t["rag_text"] is None:
            st.error("The AI service is busy. Wait a minute, then ask again.")
        else:
            render_answer(t["rag_text"])
            items = "".join(f'<p class="readitem">{html.escape(d["title"])}</p>' for d in t["docs"])
            st.markdown(f'<p class="readhead">Read {len(t["docs"])} leaflets</p>{items}', unsafe_allow_html=True)
    with right:
        st.markdown('<p class="colhead kg">LLM + RAG + KG</p>'
                    '<p class="colsub">Searches the leaflets and follows the connections</p>',
                    unsafe_allow_html=True)
        if t["kg_text"] is None:
            st.error("The AI service is busy. Wait a minute, then ask again.")
        else:
            render_answer(t["kg_text"])

    if t["edges"]:
        st.markdown('<p class="graphhead">How the KG assistant got there</p>'
                    '<p class="graphsub">Each leaflet only describes its own product. '
                    'The graph links them through their ingredients.</p>', unsafe_allow_html=True)
        svg, height = graph_html(t["edges"])
        components.html(svg, height=height + 10)
        st.markdown(LEGEND, unsafe_allow_html=True)
        with st.expander("Sources for these connections"):
            seen = set()
            for e in t["edges"]:
                if e["source"] and e["detail"] and (e["detail"], e["source"]) not in seen:
                    seen.add((e["detail"], e["source"]))
                    st.markdown(f"* **{e['src']} to {e['dst']}:** {e['detail']} "
                                f"({e['conf']} confidence). [Source]({e['source']})")
            if t["calls"]:
                st.markdown("**Graph queries run**")
                for call in t["calls"]:
                    st.code(call, language="python")
    elif t["kg_text"] is not None:
        st.markdown('<p class="meta">The graph was not needed for this answer.</p>', unsafe_allow_html=True)

    st.markdown(f'<p class="meta">Answered by {t["rag_model"]} (left) and {t["kg_model"]} (right). '
                f'Not medical advice.</p><hr class="turn-end">', unsafe_allow_html=True)

# ---------- Page ----------

st.markdown("""
<div class="hero">
  <h1>Cold and Flu Checker</h1>
  <p>Ask about common UK cold and flu remedies and compare two AI assistants side by side.
  Educational demo, not medical advice. Always read the leaflet and speak to a pharmacist.</p>
</div>
""", unsafe_allow_html=True)

with st.expander("How this comparison works"):
    st.markdown(
        "Both assistants use the same Gemini model and read the same three leaflets, chosen by searching "
        "for the ones closest to your question. Plain document search can only read a few leaflets at a "
        "time, and real catalogues hold thousands, so some will always be missed. The right hand assistant "
        "can also follow a knowledge graph that links each product to its ingredients and each ingredient "
        "to its official warnings.\n\n"
        "**Products covered:** Lemsip Max Cold & Flu Lemon, Lemsip Cold & Flu Lemon, Beechams All in One "
        "(oral solution), Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant Tablets, Paracetamol 500mg "
        "Tablets, Ibuprofen 200mg Tablets, and the withdrawn Day Nurse Capsules.\n\n"
        "**Sources:** official product information on the emc website, the NHS and the MHRA, checked by a "
        "pharmacy graduate. Every fact in the graph carries its source and a confidence level.")

EXAMPLES = [
    ("I've had paracetamol today", "I've taken paracetamol today. Which of these products should I not take as well: Lemsip Max Cold & Flu, Lemsip Cold & Flu, Beechams All in One, Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant?"),
    ("I have high blood pressure", "I have high blood pressure. Which of these products should I avoid: Lemsip Max Cold & Flu, Lemsip Cold & Flu, Beechams All in One, Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant?"),
    ("Which ones make me drowsy?", "Which of these products will make me drowsy: Lemsip Max Cold & Flu, Lemsip Cold & Flu, Beechams All in One, Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant?"),
]

if "turns" not in st.session_state:
    st.session_state.turns = []

question = None
if not st.session_state.turns:
    st.markdown('<p class="start">Start with one of these, or type your own question below.</p>',
                unsafe_allow_html=True)
    ex_cols = st.columns(3)
    for col, (label, q) in zip(ex_cols, EXAMPLES):
        if col.button(label, key=f"ex_{label}"):
            question = q

typed = st.chat_input("Ask anything about these cold and flu products", max_chars=500)
if typed and typed.strip():
    question = typed.strip()

for turn in st.session_state.turns:
    render_turn(turn)

if question:
    if len(st.session_state.turns) >= MAX_QUESTIONS:
        st.info("You've reached the 10 question limit for this visit. Refresh the page later to ask more.")
    else:
        with st.spinner("Asking both assistants..."):
            try:
                turn = run_comparison(question)
            except Exception:
                turn = None
        if turn is None:
            st.error("The search service is busy. Wait a minute, then ask again.")
        else:
            st.session_state.turns.append(turn)
            st.rerun()

st.caption("Built by Maureen Okoro, pharmacy graduate and MSc Data Science student, with Neo4j AuraDB, "
           "Gemini and Streamlit. Inspired by the knowledge graph talk at DevFest Wales 2026. "
           "Code: github.com/mauree155/cold-flu-graphrag")
