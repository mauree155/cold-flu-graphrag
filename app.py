import time
import numpy as np
import streamlit as st
from neo4j import GraphDatabase
from google import genai
from google.genai import types

st.set_page_config(page_title="Cold and Flu Checker", page_icon="💊", layout="wide")

MODELS = ["gemini-3.1-flash-lite", "gemini-2.5-flash"]
EMBED_MODELS = ["gemini-embedding-001", "text-embedding-004"]
MAX_QUESTIONS = 10

# ---------- Look and feel ----------

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700&display=swap');
html, body, [data-testid="stAppViewContainer"], [data-testid="stMarkdownContainer"],
button, textarea, input { font-family: 'Atkinson Hyperlegible', sans-serif !important; }
#MainMenu, footer { visibility: hidden; }
.block-container { max-width: 1100px; padding-top: 2.5rem; }

.hero h1 { font-size: 2.6rem; font-weight: 700; color: #1C2420; margin: 0 0 .3rem; line-height: 1.1; }
.hero .lede { font-size: 1.15rem; color: #3E4A44; max-width: 62ch; margin: 0 0 1.2rem; line-height: 1.5; }
.chain { display: flex; flex-wrap: wrap; align-items: center; gap: .45rem; margin: .4rem 0 .4rem; }
.chain .node { background: #FFFFFF; border: 1.5px solid #0E6B4F; color: #0E6B4F;
  border-radius: 999px; padding: .3rem .85rem; font-weight: 700; font-size: .95rem; }
.chain .node.risk { border-color: #B3261E; color: #B3261E; }
.chain .edge { font-size: .9rem; color: #5B6770; font-style: italic; }
.chain-note { font-size: .95rem; color: #5B6770; margin: 0 0 1.4rem; }
.notice { border-left: 4px solid #B26B00; background: #FFF8EC; padding: .75rem 1rem;
  color: #5A3A00; border-radius: 4px; margin-bottom: 1.6rem; }

.st-key-rag_panel, .st-key-kg_panel { background: #FFFFFF; border: 1.5px solid #CBD5D0;
  border-radius: 6px; padding: 1rem 1.2rem 1rem; }
.st-key-kg_panel { border-color: #0E6B4F; box-shadow: 0 0 0 3px rgba(14,107,79,.08); }
.label-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem;
  padding-bottom: .6rem; margin-bottom: .8rem; border-bottom: 1.5px dashed #CBD5D0; }
.label-head .name { font-weight: 700; font-size: 1.3rem; color: #1C2420; }
.st-key-kg_panel .label-head .name { color: #0E6B4F; }
.label-head .what { font-size: .9rem; color: #5B6770; }
.label-foot { margin-top: .8rem; padding-top: .6rem; border-top: 1.5px dashed #CBD5D0;
  font-size: .85rem; color: #5B6770; }

button[data-testid="stBaseButton-secondary"] { border-radius: 999px; border: 1.5px solid #0E6B4F;
  color: #0E6B4F; background: #FFFFFF; font-weight: 700; }
button[data-testid="stBaseButton-primary"] { background: #0E6B4F; border-color: #0E6B4F;
  color: #FFFFFF; border-radius: 6px; font-weight: 700; }
button:focus-visible { outline: 3px solid #B26B00 !important; outline-offset: 2px; }
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
RETURN p.name AS product, 'ingredient: ' + i.name AS via, type(r) AS link, r.detail AS detail,
  r.confidence AS confidence, r.verified AS verified, r.source AS source
UNION
MATCH (p:Product)-[r:CONTRAINDICATED_IN|CAUTION_IN]->(c:Condition)
WHERE toLower(c.name) CONTAINS toLower($condition)
RETURN p.name AS product, 'product leaflet' AS via, type(r) AS link, r.detail AS detail,
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
RETURN p.name AS product, c.amount AS amount, c.confidence AS confidence,
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
RETURN p.name AS product, i.name AS ingredient, e.confidence AS confidence,
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
  a.detail AS detail, a.confidence AS confidence
UNION
MATCH (p:Product)-[a:AFFECTS]->(:Activity {name: 'Driving'})
RETURN p.name AS product, 'stated on product leaflet' AS reason,
  a.detail AS detail, a.confidence AS confidence
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
    "'the tools' or 'the leaflet'; just state the facts. When covering several products, give one short "
    "bullet per product with the verdict first, for example: 'Lemsip Max Cold & Flu Lemon: avoid. Contains "
    "phenylephrine, which is contraindicated in high blood pressure.' Do not add a medical disclaimer, "
    "because the page already shows one."
)

RAG_CONFIG = types.GenerateContentConfig(system_instruction=(
    "You answer questions about UK cold and flu products using only the information provided. "
    "If information about a product is missing, end with one line: 'No information found for: ...' "
    + STYLE))

RAG_KG_CONFIG = types.GenerateContentConfig(tools=GRAPH_TOOLS, system_instruction=(
    "You answer questions about UK cold and flu products. Always use the graph tools, and also use the "
    "information provided. If they conflict, follow the more cautious official wording and say so in a few words. "
    "If neither has the answer, say so. If a product is withdrawn, say so clearly. "
    "If products share paracetamol, follow each product's own instruction: most say do not take with other "
    "paracetamol products; only apply dose spacing where a product's own dose_note allows it. "
    "Add '(not fully confirmed, check the leaflet)' after any medium or low confidence fact. "
    + STYLE))

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

def tools_used(resp):
    calls = []
    try:
        for content in resp.automatic_function_calling_history or []:
            for part in content.parts or []:
                fc = getattr(part, "function_call", None)
                if fc:
                    args = ", ".join(f"{k}={v!r}" for k, v in (fc.args or {}).items())
                    calls.append(f"{fc.name}({args})")
    except Exception:
        pass
    return calls

def format_docs(docs):
    return "\n\n".join(f"[{d['title']}] {d['text']}" for d in docs)

def label_head(name, what):
    st.markdown(f'<div class="label-head"><span class="name">{name}</span>'
                f'<span class="what">{what}</span></div>', unsafe_allow_html=True)

def label_foot(model):
    st.markdown(f'<div class="label-foot">Answered by {model}. Not medical advice.</div>',
                unsafe_allow_html=True)

# ---------- Page ----------

st.markdown("""
<div class="hero">
  <h1>Cold and Flu Checker</h1>
  <p class="lede">Ask one question about common UK cold and flu remedies. Two assistants answer it
  side by side, so you can see what a knowledge graph adds to plain document search.</p>
</div>
<div class="chain">
  <span class="node">Lemsip Max Cold &amp; Flu</span>
  <span class="edge">contains</span>
  <span class="node risk">Paracetamol</span>
  <span class="edge">also in</span>
  <span class="node">Beechams All in One</span>
</div>
<p class="chain-note">Each leaflet only describes its own product. The graph connects them.</p>
<div class="notice">Educational demo only, not medical advice. Always read the product leaflet and speak
to a pharmacist or doctor before taking any medicine.</div>
""", unsafe_allow_html=True)

EXAMPLES = [
    ("I've already had paracetamol", "I've taken paracetamol today. Which of these products should I not take as well: Lemsip Max Cold & Flu, Lemsip Cold & Flu, Beechams All in One, Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant?"),
    ("I have high blood pressure", "I have high blood pressure. Which of these products should I avoid: Lemsip Max Cold & Flu, Lemsip Cold & Flu, Beechams All in One, Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant?"),
    ("Which ones make me drowsy?", "Which of these products will make me drowsy: Lemsip Max Cold & Flu, Lemsip Cold & Flu, Beechams All in One, Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant?"),
]

if "count" not in st.session_state:
    st.session_state.count = 0

run_question = None
st.markdown("**Try one of these**")
example_cols = st.columns(len(EXAMPLES))
for col, (label, q) in zip(example_cols, EXAMPLES):
    if col.button(label, key=f"ex_{label}"):
        run_question = q

typed = st.text_area("Or ask your own question", max_chars=500, height=80,
                     placeholder="For example: Can I take Nurofen Cold & Flu and Sudafed together?")
if st.button("Compare answers", type="primary") and typed.strip():
    run_question = typed.strip()

if run_question:
    if st.session_state.count >= MAX_QUESTIONS:
        st.info("You've reached the 10 question limit for this visit. Refresh the page later to ask more.")
    else:
        st.session_state.count += 1
        st.markdown(f"#### {run_question}")
        try:
            docs = retrieve(run_question)
        except Exception:
            st.error("The search service is busy. Wait a minute, then ask again.")
            st.stop()
        prompt = f"Information:\n{format_docs(docs)}\n\nQuestion: {run_question}"
        left, right = st.columns(2, gap="large")

        with left:
            with st.container(key="rag_panel"):
                label_head("LLM + RAG", "Product documents only")
                with st.spinner("Searching documents..."):
                    resp, model = generate(prompt, RAG_CONFIG)
                if resp is None:
                    st.error("The AI service is busy. Wait a minute, then ask again.")
                else:
                    st.markdown(resp.text)
                    with st.expander("What it read"):
                        for d in docs:
                            st.markdown(f"* [{d['title']}]({d['source']})")
                    label_foot(model)

        with right:
            with st.container(key="kg_panel"):
                label_head("LLM + RAG + KG", "Documents plus knowledge graph")
                with st.spinner("Searching documents and graph..."):
                    resp, model = generate(prompt, RAG_KG_CONFIG)
                if resp is None:
                    st.error("The AI service is busy. Wait a minute, then ask again.")
                else:
                    st.markdown(resp.text)
                    with st.expander("What it read and queried"):
                        for d in docs:
                            st.markdown(f"* [{d['title']}]({d['source']})")
                        for call in tools_used(resp):
                            st.code(call, language="python")
                    label_foot(model)

with st.expander("About this demo"):
    st.markdown(
        "Both assistants use the same Gemini model and receive the same three retrieved documents. "
        "The only difference is that the right hand assistant can also query a knowledge graph linking "
        "products to ingredients, and ingredients to warnings.\n\n"
        "**Products covered:** Lemsip Max Cold & Flu Lemon, Lemsip Cold & Flu Lemon, Beechams All in One "
        "(oral solution), Night Nurse, Nurofen Cold & Flu, Sudafed Decongestant Tablets, Paracetamol 500mg "
        "Tablets, Ibuprofen 200mg Tablets, and the withdrawn Day Nurse Capsules.\n\n"
        "**Sources:** official product information on the emc website, the NHS and the MHRA, checked by a "
        "pharmacy graduate. Every fact in the graph carries its source and a confidence level.")

st.caption("Built by Maureen Okoro, pharmacy graduate and MSc Data Science student, with Neo4j AuraDB, "
           "Gemini and Streamlit. Inspired by the knowledge graph talk at DevFest Wales 2026. "
           "Code: github.com/mauree155/cold-flu-graphrag")
