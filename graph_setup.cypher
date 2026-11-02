// cold & flu knowledge graph
// run this once on an empty neo4j database, should give 30 nodes and 48 relationships
// facts come from the emc (SmPCs), nhs medicines pages and the mhra recall notice

WITH
  'https://www.medicines.org.uk/emc/product/5612/smpc' AS sLemsipMax,
  'https://www.medicines.org.uk/emc/product/617/smpc' AS sLemsip,
  'https://www.medicines.org.uk/emc/product/3913/smpc' AS sBeechams,
  'https://www.medicines.org.uk/emc/product/354/smpc' AS sNightNurse,
  'https://www.medicines.org.uk/emc/product/5626/smpc' AS sNurofen,
  'https://www.medicines.org.uk/emc/product/6199/smpc' AS sSudafed,
  'https://www.medicines.org.uk/emc/product/15314/smpc' AS sNightGeneric,
  'https://www.nhs.uk/medicines/paracetamol-for-adults/' AS sNhsPara,
  'https://www.nhs.uk/medicines/ibuprofen-for-adults/' AS sNhsIbu,
  'https://www.gov.uk/drug-device-alerts/class-2-medicines-recall-various-marketing-authorisation-holders-pholcodine-containing-products-el-23-a-slash-09' AS sMhra,
  'https://www.haleonhealthpartner.com/content/cf-consumer-healthcare/health-professionals/en_IE/Home/respiratory-health/brands/nurses/products/night-nurse.html' AS sHaleon
CREATE

// products
(lmax:Product {name: 'Lemsip Max Cold & Flu Lemon', form: 'sachet', dose_unit: '1 sachet',
  min_age: 16, age_confidence: 'high', age_note: 'Do not give to children under 16',
  max_daily_doses: 4, dose_confidence: 'high',
  dose_note: 'Maximum 4 sachets in 24 hours, for no more than 5 days', source: sLemsipMax}),
(lstd:Product {name: 'Lemsip Cold & Flu Lemon', form: 'sachet', dose_unit: '1 sachet',
  min_age: 16, age_confidence: 'high', age_note: 'Do not give to children under 16',
  max_daily_doses: 4, dose_confidence: 'high',
  dose_note: 'Maximum 4 sachets in 24 hours, for no more than 5 days', source: sLemsip}),
(bee:Product {name: 'Beechams All in One', form: 'oral solution', dose_unit: '20ml',
  min_age: 16, age_confidence: 'high', age_note: 'Not for under 16s except on medical advice',
  max_daily_doses: 4, dose_confidence: 'high',
  dose_note: 'Maximum 4 doses in 24 hours, no more than 5 days without medical advice',
  label_warning: 'Do not take with other cold, flu or decongestant products', source: sBeechams}),
(nn:Product {name: 'Night Nurse', form: 'oral solution', dose_unit: '20ml', time_of_day: 'night',
  min_age: 16, age_confidence: 'high', age_note: 'Not for under 16s except on medical advice',
  max_daily_doses: 1, dose_confidence: 'high',
  dose_note: 'One 20ml dose at bedtime, once per night, for no more than 3 days. Other paracetamol products may be taken during the day if total paracetamol stays within 4000mg in 24 hours, with at least 4 hours between doses.',
  label_warning: 'Do not use with other cough or cold medicines or any antihistamine products', source: sNightNurse}),
(nur:Product {name: 'Nurofen Cold & Flu', form: 'tablet', dose_unit: '1 or 2 tablets',
  min_age: 12, age_confidence: 'high', max_daily_doses: 6, dose_note: 'Maximum 6 tablets in 24 hours',
  dose_confidence: 'high', source: sNurofen}),
// sudafed age and dose only from retailer pages so far, hence the lower confidence
(sud:Product {name: 'Sudafed Decongestant Tablets', form: 'tablet', dose_unit: '1 tablet',
  min_age: 12, age_confidence: 'medium', max_daily_doses: 4, dose_confidence: 'low', source: sSudafed}),
(ptab:Product {name: 'Paracetamol 500mg Tablets', form: 'tablet', dose_unit: '1 or 2 tablets',
  min_age: 10, age_confidence: 'high', max_daily_doses: 4, dose_note: 'Maximum 8 tablets in 24 hours',
  dose_confidence: 'high', source: sNhsPara}),
(itab:Product {name: 'Ibuprofen 200mg Tablets', form: 'tablet',
  age_confidence: 'low', age_note: 'Not yet checked', dose_confidence: 'low', source: sNhsIbu}),

// day nurse was pulled in march 2023 because of the pholcodine, kept in so the app can say so
(dn:WithdrawnProduct {name: 'Day Nurse Capsules', status: 'Withdrawn from UK market',
  withdrawn_date: '2023-03-14', reason: 'Contained pholcodine, withdrawn by MHRA',
  confidence: 'high', source: sMhra}),

// ingredients
(para:Ingredient {name: 'Paracetamol'}),
(ibu:Ingredient {name: 'Ibuprofen'}),
(phen:Ingredient {name: 'Phenylephrine'}),
(pse:Ingredient {name: 'Pseudoephedrine'}),
(gua:Ingredient {name: 'Guaifenesin'}),
(dex:Ingredient {name: 'Dextromethorphan'}),
(pro:Ingredient {name: 'Promethazine'}),
(pho:Ingredient {name: 'Pholcodine', status: 'Withdrawn from UK market in 2023', source: sMhra}),

// ingredient classes
(analg:IngredientClass {name: 'Analgesic'}),
(nsaid:IngredientClass {name: 'NSAID'}),
(decon:IngredientClass {name: 'Decongestant'}),
(expec:IngredientClass {name: 'Expectorant'}),
(cough:IngredientClass {name: 'Cough Suppressant'}),
(antih:IngredientClass {name: 'Sedating Antihistamine'}),

// conditions and other things the warnings point to
(hbp:Condition {name: 'High Blood Pressure'}),
(liver:Condition {name: 'Liver Disease'}),
(asthma:Condition {name: 'Asthma'}),
(preg:Condition {name: 'Pregnancy'}),
(drowsy:Effect {name: 'Drowsiness'}),
(driving:Activity {name: 'Driving'}),
(alcohol:Substance {name: 'Alcohol'}),

// what each product contains
(lmax)-[:CONTAINS {amount: '1000mg', source: sLemsipMax, confidence: 'high', verified: false}]->(para),
(lmax)-[:CONTAINS {amount: '12.2mg', source: sLemsipMax, confidence: 'high', verified: false}]->(phen),
(lstd)-[:CONTAINS {amount: '650mg', source: sLemsip, confidence: 'high', verified: false}]->(para),
(lstd)-[:CONTAINS {amount: '10mg', source: sLemsip, confidence: 'high', verified: false}]->(phen),
(bee)-[:CONTAINS {amount: '500mg per 20ml', source: sBeechams, confidence: 'high', verified: false}]->(para),
(bee)-[:CONTAINS {amount: '200mg per 20ml', source: sBeechams, confidence: 'high', verified: false}]->(gua),
(bee)-[:CONTAINS {amount: '10mg per 20ml', source: sBeechams, confidence: 'high', verified: false}]->(phen),
(nn)-[:CONTAINS {amount: '1000mg per 20ml', source: sNightNurse, confidence: 'high', verified: false}]->(para),
(nn)-[:CONTAINS {amount: '20mg per 20ml', source: sNightNurse, confidence: 'high', verified: false}]->(pro),
(nn)-[:CONTAINS {amount: '15mg per 20ml', source: sNightNurse, confidence: 'high', verified: false}]->(dex),
(nur)-[:CONTAINS {amount: '200mg', source: sNurofen, confidence: 'high', verified: false}]->(ibu),
(nur)-[:CONTAINS {amount: '30mg', source: sNurofen, confidence: 'high', verified: false}]->(pse),
(sud)-[:CONTAINS {amount: '60mg', source: sSudafed, confidence: 'high', verified: false}]->(pse),
(ptab)-[:CONTAINS {amount: '500mg', source: sNhsPara, confidence: 'high', verified: false}]->(para),
(itab)-[:CONTAINS {amount: '200mg', source: sNhsIbu, confidence: 'medium', verified: false}]->(ibu),
// only the pholcodine is confirmed by the mhra notice, the other two amounts are from old retail listings
(dn)-[:CONTAINED {source: sMhra, confidence: 'high', verified: false}]->(pho),
(dn)-[:CONTAINED {amount: '500mg', source: 'Retail listings', confidence: 'medium', verified: false}]->(para),
(dn)-[:CONTAINED {amount: '30mg', source: 'Retail listings', confidence: 'medium', verified: false}]->(pse),

// ingredient classes
(para)-[:BELONGS_TO {source: sLemsipMax, confidence: 'high'}]->(analg),
(ibu)-[:BELONGS_TO {source: sNurofen, confidence: 'high'}]->(nsaid),
(phen)-[:BELONGS_TO {source: sBeechams, confidence: 'high'}]->(decon),
(pse)-[:BELONGS_TO {source: sNurofen, confidence: 'high'}]->(decon),
(gua)-[:BELONGS_TO {source: sBeechams, confidence: 'high'}]->(expec),
(dex)-[:BELONGS_TO {source: sNightNurse, confidence: 'high'}]->(cough),
(pro)-[:BELONGS_TO {source: sNightNurse, confidence: 'high'}]->(antih),
(pho)-[:BELONGS_TO {source: sMhra, confidence: 'high'}]->(cough),

// warnings on the ingredient, so every product containing it picks them up
// phenylephrine + hypertension is worded the same in both lemsips and beechams, checked in section 4.3
(phen)-[:CONTRAINDICATED_IN {detail: 'Contraindicated in hypertension (emc SmPC section 4.3)', source: sLemsipMax, confidence: 'high', verified: true}]->(hbp),
(ibu)-[:CAUTION_IN {detail: 'Speak to a pharmacist or doctor first', source: sNurofen, confidence: 'high', verified: false}]->(hbp),
(para)-[:CAUTION_IN {detail: 'Liver disease increases the risk of paracetamol related liver damage', source: sNightGeneric, confidence: 'high', verified: false}]->(liver),
(ibu)-[:CAUTION_IN {detail: 'May trigger bronchospasm in people with asthma', source: sNurofen, confidence: 'high', verified: false}]->(asthma),
(ibu)-[:CONTRAINDICATED_IN {detail: 'Last trimester of pregnancy', source: sNurofen, confidence: 'high', verified: false}]->(preg),
(pro)-[:CAUTION_IN {detail: 'Safety in pregnancy not established', source: sNightGeneric, confidence: 'high', verified: false}]->(preg),
(phen)-[:CAUTION_IN {detail: 'Safety in pregnancy not established', source: sBeechams, confidence: 'high', verified: false}]->(preg),
(gua)-[:CAUTION_IN {detail: 'Safety in pregnancy not established', source: sBeechams, confidence: 'high', verified: false}]->(preg),

// warnings on the product itself, where the leaflet wording is specific to that product
// pseudoephedrine sits here rather than on the ingredient: sudafed bans it in hypertension,
// but nurofen only in severe or uncontrolled hypertension
(sud)-[:CONTRAINDICATED_IN {detail: 'Must not be used in cardiovascular disease including hypertension', source: sSudafed, confidence: 'high', verified: false}]->(hbp),
(nur)-[:CONTRAINDICATED_IN {detail: 'Contraindicated in severe or uncontrolled hypertension. With other high blood pressure, speak to a pharmacist or doctor first.', source: sNurofen, confidence: 'high', verified: false}]->(hbp),
// night nurse leaflet says cardiovascular problems, not hypertension by name, so medium confidence
(nn)-[:CAUTION_IN {detail: 'Seek medical advice first in cardiovascular problems (SmPC 4.4). Hypertension is not named specifically.', source: sNightNurse, confidence: 'medium', verified: true}]->(hbp),
(nn)-[:CONTRAINDICATED_IN {detail: 'Contraindicated during an asthma attack or exacerbation, or with risk of respiratory failure. Seek advice with chronic cough such as in asthma.', source: sNightNurse, confidence: 'high', verified: true}]->(asthma),
(bee)-[:CAUTION_IN {detail: 'Seek medical advice first with chronic cough, such as with asthma, smoking, chronic bronchitis or emphysema', source: sBeechams, confidence: 'high', verified: true}]->(asthma),
(bee)-[:CONTRAINDICATED_IN {detail: 'Contraindicated in hepatic impairment', source: sBeechams, confidence: 'high', verified: false}]->(liver),
// this is what catches beechams + night nurse (dextromethorphan is a cough suppressant)
(bee)-[:AVOID_WITH {detail: 'Do not take with a cough suppressant', source: sBeechams, confidence: 'high', verified: true}]->(cough),

// drowsiness, driving and interactions
(pro)-[:CAUSES {frequency: 'very common', source: sHaleon, confidence: 'high', verified: false}]->(drowsy),
(drowsy)-[:AFFECTS {detail: 'Drowsiness can seriously affect the ability to drive. Do not drive if affected.', source: sNightNurse, confidence: 'high', verified: true}]->(driving),
(nn)-[:AFFECTS {detail: 'May cause drowsiness, dizziness, blurred vision and impaired thinking that can seriously affect driving. Do not drive if affected. Listed under section 5a of the Road Traffic Act 1988: likely to affect driving, and it is an offence to drive while impaired by it.', source: sNightNurse, confidence: 'high', verified: true}]->(driving),
// beechams isn't sedating but it's 19% alcohol, and the leaflet mentions dizziness
(bee)-[:AFFECTS {detail: 'Contains alcohol (19% v/v). Do not drive or operate machinery if affected by dizziness.', source: sBeechams, confidence: 'high', verified: false}]->(driving),
(pro)-[:INTERACTS_WITH {effect: 'Increased drowsiness', source: sNightNurse, confidence: 'high', verified: false}]->(alcohol),
(dex)-[:INTERACTS_WITH {effect: 'Increased drowsiness', source: sNightNurse, confidence: 'high', verified: false}]->(alcohol),
// lemsip says don't combine with other decongestants, nurofen says take care, so went with the stricter one
(pse)-[:INTERACTS_WITH {effect: 'Do not take two sympathomimetic decongestants together', severity: 'avoid', source: sLemsipMax, second_source: sNurofen, confidence: 'high', verified: true}]->(phen)

// quick checks after loading:
// MATCH (n) RETURN labels(n)[0] AS type, count(*) AS total ORDER BY type
// MATCH ()-[r]->() RETURN count(r)
